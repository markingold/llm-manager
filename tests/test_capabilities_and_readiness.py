from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from llm_manager import model_inspector, server


class _Response:
    def __init__(self, model_ids: list[str], status_code: int = 200):
        self.status_code = status_code
        self.ok = status_code == 200
        self._model_ids = model_ids

    def json(self):
        return {"data": [{"id": model_id} for model_id in self._model_ids]}


def _model(name: str, config: dict | None = None) -> Path:
    path = server.MODELS_DIR / name
    path.mkdir()
    (path / "config.json").write_text(json.dumps(config or {"model_type": "llama"}))
    return path


def test_model_inspector_identifies_sentence_transformer_embedding_capability():
    path = _model("embedder", {"architectures": ["BertModel"]})
    (path / "modules.json").write_text("[]")
    inspected = model_inspector.inspect_one("embedder")
    assert inspected["capabilities"] == ["embeddings"]
    assert inspected["recommended_backend"] == "vllm"


def test_openrouter_catalog_uses_output_modality_as_authoritative_capability():
    embedding = server._build_openrouter_catalog_model(
        {
            "id": "provider/vector-model",
            "name": "Vector Model",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["embeddings"]},
        },
        {},
    )
    misleading_name = server._build_openrouter_catalog_model(
        {
            "id": "provider/not-an-embedding-model",
            "name": "Embedding In Name Only",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        },
        {},
    )
    assert embedding["capabilities"] == ["embeddings"]
    assert misleading_name["capabilities"] == ["chat", "completions"]


def test_embedding_slot_requires_embedding_metadata_and_vllm(monkeypatch: pytest.MonkeyPatch):
    _model("chat-only")
    with pytest.raises(HTTPException) as exc:
        server.switch_model("embed", "chat-only", bounce=False, backend="vllm")
    assert exc.value.status_code == 422

    _model("embedder", {"llm_manager_capabilities": ["embeddings"]})
    monkeypatch.setattr(server, "_vllm_backend_available", lambda: True)
    monkeypatch.setattr(server, "_bounce_engine", lambda _: {"ok": True, "method": "test"})
    monkeypatch.setattr(server, "_wait_for_model_readiness", lambda *args, **kwargs: {"ok": True, "matched_model_id": "embedder"})
    result = server.switch_model("embed", "embedder", bounce=True, backend="vllm")
    assert result["backend"] == "vllm"
    assert result["model_capabilities"] == ["embeddings"]
    assert (server.MODELS_DIR / "embed_active_model").resolve().name == "embedder"


def test_embedding_slot_fails_when_vllm_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    _model("embedder", {"llm_manager_capabilities": ["embeddings"]})
    monkeypatch.setattr(server, "_vllm_backend_available", lambda: False)
    with pytest.raises(HTTPException) as exc:
        server.switch_model("embed", "embedder", bounce=False)
    assert exc.value.status_code == 400
    assert "available vllm runtime" in str(exc.value.detail)


def test_readiness_poll_requires_an_exact_reported_model(monkeypatch: pytest.MonkeyPatch):
    expected = _model("expected")
    responses = iter([_Response(["expected-but-wrong"]), _Response([str(expected.resolve())])])
    monkeypatch.setattr(server.requests, "get", lambda *args, **kwargs: next(responses))
    result = server._wait_for_model_readiness("chat", expected, "tgw", timeout_seconds=0.2)
    assert result["ok"] is True
    assert result["attempts"] == 2
    assert result["matched_model_id"] == str(expected.resolve())


def test_readiness_timeout_rolls_back_model_and_backend(monkeypatch: pytest.MonkeyPatch):
    old = _model("old")
    _model("new")
    link = server.MODELS_DIR / "chat_active_model"
    link.symlink_to(old, target_is_directory=True)
    server.write_slot_backends({**server.DEFAULT_SLOT_BACKENDS, "chat": "tgw"})
    monkeypatch.setattr(server, "_bounce_engine", lambda _: {"ok": True, "method": "test"})
    monkeypatch.setattr(
        server,
        "_wait_for_model_readiness",
        lambda *args, **kwargs: {"ok": False, "detail": "wrong model", "model_ids": ["other"]},
    )
    with pytest.raises(HTTPException) as exc:
        server.switch_model("chat", "new", bounce=True, backend="tgw")
    assert exc.value.status_code == 504
    assert link.resolve() == old.resolve()
    assert server.read_slot_backends()["chat"] == "tgw"
