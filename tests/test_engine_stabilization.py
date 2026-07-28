from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llm_manager import server
from llm_manager.runtime_env import read_runtime_env


def _load_engine_launcher():
    path = Path(__file__).resolve().parents[1] / "run" / "engine_launcher.py"
    spec = importlib.util.spec_from_file_location("engine_launcher_stabilization_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _model(root: Path, name: str) -> Path:
    model = root / name
    model.mkdir()
    (model / "config.json").write_text('{"model_type":"llama"}')
    return model


def test_runtime_env_uses_project_fallback_without_overriding_process_or_global(tmp_path: Path):
    project = tmp_path / "project.env"
    shared = tmp_path / "global.env"
    project.write_text("TABBYAPI_CMD=project-command\nLOCAL_ONLY=project\n")
    shared.write_text("TABBYAPI_CMD=shared-command\nSHARED_ONLY=shared\n")

    effective = read_runtime_env(
        project_env_path=project,
        global_env_path=shared,
        process_env={"PROCESS_ONLY": "process", "TABBYAPI_CMD": "process-command"},
    )

    assert effective == {
        "LOCAL_ONLY": "project",
        "SHARED_ONLY": "shared",
        "PROCESS_ONLY": "process",
        "TABBYAPI_CMD": "process-command",
    }


def test_missing_backend_is_static_configuration_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    launcher = _load_engine_launcher()
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    _model(models, "checkpoint")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "slot_backends.json").write_text('{"chat":"tabbyapi"}')

    monkeypatch.setattr(launcher, "MODELS_DIR", str(models))
    monkeypatch.setattr(launcher, "SLOT_BACKENDS_PATH", state / "slot_backends.json")
    monkeypatch.setattr(launcher, "_detect_kind", lambda _path: "exl2")
    monkeypatch.setattr(
        launcher,
        "probe_backend",
        lambda *_args, **_kwargs: {"available": False, "detail": "configured executable is unavailable"},
    )

    with pytest.raises(launcher.EnginePreflightError) as exc:
        launcher.preflight_launch(
            api_port="8500",
            model="checkpoint",
            max_seq_len="8192",
            listen_host="127.0.0.1",
            env={},
            check_port=False,
        )

    assert exc.value.failure_type == "missing_backend"
    assert exc.value.exit_code == launcher.EX_CONFIG == 78
    assert exc.value.retryable is False


def test_missing_or_broken_model_alias_fails_before_backend_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    launcher = _load_engine_launcher()
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    (models / "chat_active_model").symlink_to(models / "removed-checkpoint", target_is_directory=True)
    monkeypatch.setattr(launcher, "MODELS_DIR", str(models))
    monkeypatch.setattr(
        launcher,
        "probe_backend",
        lambda *_args, **_kwargs: pytest.fail("backend probe must not run for a broken alias"),
    )

    with pytest.raises(launcher.EnginePreflightError) as exc:
        launcher.preflight_launch(
            api_port="8500",
            model="chat_active_model",
            max_seq_len="8192",
            listen_host="127.0.0.1",
            env={},
            check_port=False,
        )

    assert exc.value.failure_type == "missing_model"
    assert exc.value.exit_code == 78


def test_port_collision_is_temporary_and_distinct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    launcher = _load_engine_launcher()
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    _model(models, "checkpoint")
    monkeypatch.setattr(launcher, "MODELS_DIR", str(models))
    monkeypatch.setattr(launcher, "SLOT_BACKENDS_PATH", tmp_path / "missing-slot-backends.json")
    monkeypatch.setattr(launcher, "_detect_kind", lambda _path: "transformers")
    monkeypatch.setattr(
        launcher,
        "probe_backend",
        lambda *_args, **_kwargs: {"available": True, "detail": None},
    )
    monkeypatch.setattr(launcher, "_port_available", lambda *_args: False)

    with pytest.raises(launcher.EnginePreflightError) as exc:
        launcher.preflight_launch(
            api_port="8500",
            model="checkpoint",
            max_seq_len="8192",
            listen_host="127.0.0.1",
            env={},
            check_port=True,
        )

    assert exc.value.failure_type == "port_collision"
    assert exc.value.exit_code == launcher.EX_TEMPFAIL == 75
    assert exc.value.retryable is True


def test_failed_preflight_never_executes_and_redacts_event(monkeypatch: pytest.MonkeyPatch, capsys):
    launcher = _load_engine_launcher()
    monkeypatch.setattr(launcher, "_runtime_env", lambda: {})
    monkeypatch.setattr(
        launcher,
        "preflight_launch",
        lambda **_kwargs: (_ for _ in ()).throw(
            launcher.EnginePreflightError(
                "missing_backend",
                "api_key=sk-this-must-not-appear",
            )
        ),
    )
    monkeypatch.setattr(launcher.os, "execv", lambda *_args: pytest.fail("exec must not run"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "engine_launcher.py",
            "--api-port",
            "8500",
            "--model",
            "chat_active_model",
            "--max-seq-len",
            "8192",
        ],
    )

    assert launcher.main() == 78
    event = json.loads(capsys.readouterr().err)
    assert event["event"] == "engine_preflight_failed"
    assert event["failure_type"] == "missing_backend"
    assert event["incident_key"].startswith("engine_preflight:chat:missing_backend:")
    assert "sk-this" not in json.dumps(event)
    assert "[REDACTED]" in event["detail"]


def test_repository_units_bound_restarts_and_suppress_static_config_errors():
    root = Path(__file__).resolve().parents[1]
    unit_paths = [
        root / "deploy/systemd/llm-manager-engine@.service",
        root / "deploy/systemd/llm-embed.service",
        root / "deploy/systemd/host/llm-a.service",
        root / "deploy/systemd/host/llm-b.service",
        root / "deploy/systemd/host/llm-c.service",
        root / "deploy/systemd/host/llm-tgw-webui.service",
    ]
    for path in unit_paths:
        content = path.read_text()
        assert "Restart=on-failure" in content
        assert "RestartPreventExitStatus=78" in content
        assert "StartLimitIntervalSec=60" in content
        assert "StartLimitBurst=5" in content
        assert "Restart=always" not in content


def test_lane_configuration_failure_is_not_reported_ready(monkeypatch: pytest.MonkeyPatch):
    model = _model(server.MODELS_DIR, "chat-model")
    (server.MODELS_DIR / "chat_active_model").symlink_to(model, target_is_directory=True)
    monkeypatch.setenv("SYSTEMD_LLM_A", "llm-a.service")
    monkeypatch.setattr(
        server,
        "_systemctl_show",
        lambda _unit: {
            "ActiveState": "failed",
            "SubState": "failed",
            "UnitFileState": "enabled",
            "Result": "exit-code",
            "ExecMainStatus": "78",
        },
    )
    monkeypatch.setattr(server, "_is_listening", lambda _port: False)

    state = server._local_lane_availability(
        "chat_active_model",
        server.read_env(),
        provider_models={},
        fallback_mode="chat",
    )

    assert state["available"] is False
    assert state["state"] == "configuration_error"
    assert state["failure_type"] == "configuration_error"
    assert state["retryable"] is False
    assert "incident_key" in state


def test_ready_returns_503_with_capability_degradation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        server,
        "_readiness_snapshot",
        lambda *_args, **_kwargs: {
            "ok": False,
            "degraded": True,
            "provider_config_ok": True,
            "required_capabilities": ["chat", "completions", "embeddings"],
            "capabilities": {
                "chat": {"available": False, "sources": []},
                "completions": {"available": False, "sources": []},
                "embeddings": {"available": True, "sources": [{"provider": "local", "lane": "embed"}]},
            },
            "lanes": {
                "chat": {
                    "available": False,
                    "state": "configuration_error",
                    "failure_type": "configuration_error",
                }
            },
            "unavailable_lanes": ["chat"],
            "remote_credentials": {"openrouter": "[MISSING]", "openai": "[MISSING]"},
        },
    )

    with TestClient(server.app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["degraded"] is True
    assert body["lanes"]["chat"]["failure_type"] == "configuration_error"
    assert body["remote_credentials"] == {"openrouter": "[MISSING]", "openai": "[MISSING]"}
