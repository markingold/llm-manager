from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


class _CurrentModelResponse:
    status_code = 200
    ok = True

    def __init__(self, model_id: str):
        self._model_id = model_id

    def json(self):
        return {"id": self._model_id, "object": "model"}


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


def test_vllm_availability_requires_a_successful_module_import(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "read_env", lambda: {"VLLM_PYTHON_BIN": sys.executable})
    monkeypatch.setattr(server.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1))
    monkeypatch.setattr(server.importlib.util, "find_spec", lambda _name: None)
    assert server._vllm_backend_available() is False

    monkeypatch.setattr(server.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    assert server._vllm_backend_available() is True


def test_models_lists_supported_chat_models_for_unloaded_intent_slot():
    chat = _model("general-chat")
    lora = _model("lora_legacy-intent")
    (lora / "adapter_config.json").write_text("{}")
    _model("classifier", {"architectures": ["BertForSequenceClassification"]})
    _model("embedder", {"llm_manager_capabilities": ["embeddings"]})
    _model("vision", {"vision_config": {}})
    (server.MODELS_DIR / "chat_active_model").symlink_to(chat, target_is_directory=True)
    (server.MODELS_DIR / "webui_active_model").symlink_to(chat, target_is_directory=True)

    result = server.models()

    expected_text_models = ["general-chat"]
    assert result["chat"] == expected_text_models
    assert result["intent"] == expected_text_models
    assert result["small"] == expected_text_models
    assert result["embed"] == ["embedder"]
    assert result["active"]["intent"] is None
    assert not (server.MODELS_DIR / "intent_active_model").is_symlink()
    assert set(result["meta"]) == {
        "classifier",
        "embedder",
        "general-chat",
        "lora_legacy-intent",
        "vision",
    }
    assert result["meta"]["lora_legacy-intent"]["capabilities"] == []
    assert result["meta"]["lora_legacy-intent"]["recommended_backend"] == "unsupported"
    assert "base model" in result["meta"]["lora_legacy-intent"]["unsupported_reason"]
    assert result["meta"]["lora_legacy-intent"]["tgw_args"] == []


def test_inspector_uses_authoritative_exllama_metadata_and_current_tgw_loaders():
    exl2 = _model("opaque-exl2", {"quantization_config": {"quant_method": "exl2"}})
    exl3 = _model("opaque-exl3", {"quantization_config": {"quant_method": "exl3"}})

    assert model_inspector.detect_kind(exl2) == "exl2"
    assert model_inspector.detect_loader("exl2") is None
    assert model_inspector.detect_kind(exl3) == "exl3"
    assert model_inspector.detect_loader("exl3") == "ExLlamav3"
    assert model_inspector.detect_loader("transformers") == "Transformers"
    assert server._backend_supports_model_kind("tabbyapi", "exl2") is True
    assert server._backend_supports_model_kind("tgw", "exl2") is False
    assert server._backend_supports_model_kind("vllm", "awq") is True
    assert server._backend_supports_model_kind("tgw", "awq") is False


def test_readiness_poll_requires_an_exact_reported_model(monkeypatch: pytest.MonkeyPatch):
    expected = _model("expected")
    responses = iter([_Response(["expected-but-wrong"]), _Response([str(expected.resolve())])])
    monkeypatch.setattr(server.requests, "get", lambda *args, **kwargs: next(responses))
    result = server._wait_for_model_readiness("chat", expected, "tgw", timeout_seconds=0.2)
    assert result["ok"] is True
    assert result["attempts"] == 2
    assert result["matched_model_id"] == str(expected.resolve())


def test_tabby_readiness_uses_current_model_instead_of_inventory(monkeypatch: pytest.MonkeyPatch):
    expected = _model("expected")
    responses = iter([_CurrentModelResponse("other"), _CurrentModelResponse("expected")])
    requested_urls = []

    def get(url, **_kwargs):
        requested_urls.append(url)
        return next(responses)

    monkeypatch.setattr(server.requests, "get", get)
    result = server._wait_for_model_readiness("chat", expected, "tabbyapi", timeout_seconds=0.2)

    assert result["ok"] is True
    assert result["attempts"] == 2
    assert result["matched_model_id"] == "expected"
    assert requested_urls == ["http://127.0.0.1:8500/v1/model"] * 2


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
