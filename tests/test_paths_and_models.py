from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from llm_manager import backend_registry, model_inspector, server


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


def test_llamacpp_selects_one_managed_gguf_and_rejects_ambiguity(tmp_path: Path):
    launcher = _load_run_script("launch_llamacpp.py")
    model = tmp_path / "gguf-model"
    model.mkdir()
    only = model / "model-q6.gguf"
    only.write_bytes(b"GGUF")
    assert launcher._select_gguf(model) == only

    (model / "model-q4.gguf").write_bytes(b"GGUF")
    with pytest.raises(SystemExit):
        launcher._select_gguf(model)
    assert launcher._select_gguf(model, "model-q4.gguf").name == "model-q4.gguf"


def test_lora_is_exposed_only_with_a_managed_base_model():
    base = server.MODELS_DIR / "base"
    base.mkdir()
    (base / "config.json").write_text('{"model_type":"llama"}')
    adapter = server.MODELS_DIR / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text('{"base_model_name_or_path":"base"}')

    inspected = model_inspector.inspect_one("adapter")
    assert inspected["kind"] == "lora"
    assert inspected["capabilities"] == ["chat", "completions"]
    assert inspected["recommended_backend"] == "vllm"
    assert inspected["base_model"] == "base"


def test_vllm_lora_launcher_serves_the_managed_base_and_named_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    launcher = _load_run_script("launch_vllm.py")
    models = tmp_path / "models"
    base = models / "base"
    base.mkdir(parents=True)
    (base / "config.json").write_text('{"model_type":"llama","max_position_embeddings":2048}')
    adapter = models / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text('{"base_model_name_or_path":"base"}')
    captured = {}
    monkeypatch.setattr(launcher, "_resolve_vllm_python_bin", lambda: sys.executable)

    def execv(executable, argv):
        captured.update({"executable": executable, "argv": argv})
        raise SystemExit(0)

    monkeypatch.setattr(launcher.os, "execv", execv)
    monkeypatch.setattr(sys, "argv", [
        "launch_vllm.py",
        "--api-port", "8598",
        "--model", "adapter",
        "--model-dir", str(models),
        "--max-seq-len", "2048",
    ])
    with pytest.raises(SystemExit):
        launcher.main()

    argv = captured["argv"]
    assert argv[argv.index("--model") + 1] == str(base)
    assert argv[argv.index("--served-model-name") + 1] == "base"
    assert argv[argv.index("--lora-modules") + 1] == f"adapter={adapter}"
    assert "--enable-lora" in argv


def test_backend_registry_is_available_without_exposing_command_arguments():
    result = server.backends()
    assert {"tgw", "tabbyapi", "vllm", "llamacpp"} == set(result["backends"])
    for row in result["backends"].values():
        assert "command" not in row
        assert row["model_kinds"]


def test_tabby_runtime_fails_closed_when_revision_does_not_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    root = tmp_path / "tabby"
    root.mkdir()
    monkeypatch.setattr(backend_registry, "_git_revision", lambda _path: "installed")
    row = backend_registry.probe_backend(
        "tabbyapi",
        env={
            "TABBYAPI_CMD": f"{executable} {root / 'main.py'}",
            "TABBYAPI_ROOT": str(root),
            "TABBYAPI_REVISION": "required",
        },
    )
    assert row["available"] is False
    assert row["revision_matches"] is False
    assert "revision mismatch" in row["detail"].lower()


def test_legacy_cli_cannot_switch_symlinks_or_launch_inference():
    root = Path(__file__).resolve().parents[1] / "app" / "src" / "llm_manager"
    switcher = (root / "switch_model.py").read_text()
    training_cli = (root / "main.py").read_text()
    for source in (switcher, training_cli):
        assert ".symlink_to(" not in source
        assert "pm2" not in source.lower()
        assert "subprocess.Popen" not in source
    assert "POST /models/load" in switcher


def test_engine_status_uses_slot_specific_resource_controls(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_CHAT_CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setenv("LLM_CHAT_VLLM_GPU_MEMORY_UTILIZATION", "0.85")
    row = server._engine_def("chat", env={"CUDA_VISIBLE_DEVICES": "1"}, provider_models={})
    assert row["resources"]["cuda_visible_devices"] == "0"
    assert row["resources"]["vllm_gpu_memory_utilization"] == "0.85"
