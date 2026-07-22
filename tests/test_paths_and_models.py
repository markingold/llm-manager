from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from llm_manager import model_inspector, server


def _load_engine_launcher():
    path = Path(__file__).resolve().parents[1] / "run" / "engine_launcher.py"
    spec = importlib.util.spec_from_file_location("engine_launcher_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_run_script(name: str):
    path = Path(__file__).resolve().parents[1] / "run" / name
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_path_boundary_accepts_children_and_rejects_escape(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    child = root / "child"
    child.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    assert server._resolve_path_within(child, [root], must_exist=True, must_be_dir=True) == child.resolve()
    with pytest.raises(HTTPException):
        server._resolve_path_within(outside, [root], must_exist=True, must_be_dir=True)

    escaping_link = root / "escape"
    escaping_link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(HTTPException):
        server._resolve_path_within(escaping_link, [root], must_exist=True, must_be_dir=True)


@pytest.mark.parametrize("repo_id", ["../secret", "/absolute/model", "owner/../../secret", "owner/model/extra", "owner model"])
def test_hugging_face_repo_ids_cannot_be_paths(repo_id: str):
    with pytest.raises(HTTPException):
        server._validate_repo_id(repo_id)


def test_job_paths_are_confined_to_managed_roots(tmp_path: Path):
    outside = tmp_path / "private.jsonl"
    outside.write_text("{}\n")
    with pytest.raises(HTTPException):
        server._validate_job_path_args("train", {"data_path": str(outside)}, {})


def test_multimodal_model_is_rejected_by_every_local_backend():
    model_dir = server.MODELS_DIR / "vision-model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(json.dumps({"model_type": "qwen", "vision_config": {}}))

    inspected = model_inspector.inspect_one("vision-model")
    assert inspected["kind"] == "multimodal"
    assert inspected["recommended_backend"] == "unsupported"
    assert inspected["fallback_backends"] == []
    assert all(not server._backend_supports_model_kind(backend, "multimodal") for backend in server.SUPPORTED_BACKENDS)

    with pytest.raises(HTTPException) as exc:
        server.switch_model("chat", "vision-model", bounce=False)
    assert exc.value.status_code == 422
    assert not (server.MODELS_DIR / "chat_active_model").exists()


def test_engine_launcher_enforces_the_same_model_boundary_and_multimodal_guard(tmp_path: Path):
    launcher = _load_engine_launcher()
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    launcher.MODELS_DIR = str(models)
    vision = models / "vision"
    vision.mkdir()
    (vision / "config.json").write_text(json.dumps({"vision_config": {}}))

    assert launcher._backend_supports_kind("tgw", "multimodal") is False
    assert launcher._backend_supports_kind("tgw", "exl2") is False
    assert launcher._backend_supports_kind("tabbyapi", "exl2") is True
    with pytest.raises(SystemExit):
        launcher._resolve_model_path(str(tmp_path / "outside"))


def test_inspector_will_not_read_outside_model_root(tmp_path: Path):
    outside = tmp_path / "outside-model"
    outside.mkdir()
    (outside / "config.json").write_text("{}")
    result = model_inspector.inspect_one(str(outside))
    assert result["exists"] is False


def test_tgw_launcher_uses_absolute_server_runtime_and_slot_bind_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    launcher = _load_run_script("launch_tgw.py")
    models = tmp_path / "models"
    model = models / "checkpoint"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}")
    webui = tmp_path / "text-generation-webui"
    webui.mkdir()
    server_script = webui / "server.py"
    server_script.write_text("# test fixture\n")

    runtime_env = {
        "WEBUI_ROOT": str(webui),
        "TGW_PYTHON_BIN": sys.executable,
        "TGW_WEBUI_BIND_HOST": "0.0.0.0",
    }
    captured = {}
    monkeypatch.setattr(launcher, "MODELS_DIR", str(models))
    monkeypatch.setattr(launcher, "read_runtime_env", lambda: runtime_env)
    monkeypatch.setattr(launcher, "_apply_startup_guardrails", lambda *_args: None)
    monkeypatch.setattr(launcher.os, "chdir", lambda path: captured.update({"cwd": path}))

    def execve(executable, argv, env):
        captured.update({"executable": executable, "argv": argv, "env": env})
        raise SystemExit(0)

    monkeypatch.setattr(launcher.os, "execve", execve)
    monkeypatch.setattr(sys, "argv", [
        "launch_tgw.py",
        "--api-port", "8501",
        "--model", "checkpoint",
        "--model-dir", str(models),
        "--max-seq-len", "8192",
        "--listen-host", "127.0.0.1",
        "--no-webui",
    ])

    with pytest.raises(SystemExit) as exc:
        launcher.main()
    assert exc.value.code == 0
    assert captured["executable"] == sys.executable
    assert captured["argv"][1] == str(server_script)
    listen_index = captured["argv"].index("--listen-host")
    assert captured["argv"][listen_index + 1] == "127.0.0.1"
    assert captured["cwd"] == webui.resolve()


def test_vllm_embedding_context_is_clamped_to_checkpoint_metadata(tmp_path: Path):
    launcher = _load_run_script("launch_vllm.py")
    model = tmp_path / "embedder"
    model.mkdir()
    (model / "config.json").write_text(json.dumps({"max_position_embeddings": 512}))

    assert launcher._effective_max_model_len(model, "8192", "embed") == "512"
    assert launcher._effective_max_model_len(model, "8192", "generate") == "8192"
