from __future__ import annotations

import importlib.util
import json
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
    with pytest.raises(SystemExit):
        launcher._resolve_model_path(str(tmp_path / "outside"))


def test_inspector_will_not_read_outside_model_root(tmp_path: Path):
    outside = tmp_path / "outside-model"
    outside.mkdir()
    (outside / "config.json").write_text("{}")
    result = model_inspector.inspect_one(str(outside))
    assert result["exists"] is False
