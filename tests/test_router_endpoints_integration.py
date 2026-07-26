from __future__ import annotations

import copy

import pytest
import requests
from fastapi.testclient import TestClient

from llm_manager import server


def _policies(chain: list[str]) -> dict:
    policies = copy.deepcopy(server.DEFAULT_PROVIDER_POLICIES)
    policies["schema_version"] = 2
    policies["defaults"].update({"strategy": "local_first", "allow_fallbacks": True})
    policies["selection"]["local_first"] = chain
    policies["dynamic_ranking"]["enabled"] = False
    policies["budget"]["providers"] = {"local": False, "openrouter": False, "openai": False}
    policies["task_overrides"] = {}
    return policies


def _catalog() -> dict:
    return {
        "schema_version": 2,
        "local": {
            "slots": [
                {
                    "id": "chat",
                    "label": "Chat",
                    "enabled": True,
                    "backend": "tgw",
                    "base_env": "LLM_CHAT_API_BASE",
                    "capabilities": ["chat", "completions"],
                },
                {
                    "id": "embed",
                    "label": "Embeddings",
                    "enabled": True,
                    "backend": "vllm",
                    "base_env": "LLM_EMBED_API_BASE",
                    "capabilities": ["embeddings"],
                },
            ]
        },
        "openrouter": {
            "free": [],
            "paid": [
                {
                    "id": "router/embed",
                    "enabled": True,
                    "priority": 0,
                    "capabilities": ["embeddings"],
                },
                {
                    "id": "router/chat",
                    "enabled": True,
                    "priority": 1,
                    "capabilities": ["chat", "completions"],
                }
            ],
        },
        "openai": {
            "allowed": [
                {
                    "id": "direct/embed",
                    "enabled": True,
                    "priority": 0,
                    "capabilities": ["embeddings"],
                },
                {
                    "id": "direct/chat",
                    "enabled": True,
                    "priority": 1,
                    "capabilities": ["chat", "completions"],
                }
            ]
        },
    }


def _configure(catalog: dict, policies: dict) -> None:
    server._write_json(server.PROVIDER_MODELS_PATH, catalog)
    server._write_json(server.PROVIDER_POLICIES_PATH, policies)
    server.write_slot_backends({**server.DEFAULT_SLOT_BACKENDS, "embed": "vllm"})
    for mode in ("chat", "embed"):
        model = server.MODELS_DIR / f"{mode}-checkpoint"
        model.mkdir(exist_ok=True)
        (model / "config.json").write_text("{}")
        (server.MODELS_DIR / f"{mode}_active_model").symlink_to(model, target_is_directory=True)


def test_embed_endpoint_uses_authoritative_local_embedding_slot(monkeypatch: pytest.MonkeyPatch):
    _configure(_catalog(), _policies(["local", "openai"]))
    server.write_env({"LLM_EMBED_API_BASE": "http://embed.test:8503"})
    monkeypatch.setattr(server, "_maybe_refresh_openrouter_catalog", lambda *_args, **_kwargs: {})

    captured: dict = {}

    def dispatch(provider, env, payload, provider_models=None):
        captured.update({"provider": provider, "payload": payload, "base": env["LLM_EMBED_API_BASE"]})
        return {
            "data": [{"index": 0, "embedding": [0.25, 0.5]}],
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        }

    monkeypatch.setattr(server, "_dispatch_provider_embeddings", dispatch)
    with TestClient(server.app) as client:
        response = client.post("/router/embed", json={"input": "hello"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "local"
    assert body["model"] == "embed_active_model"
    assert body["data"][0]["embedding"] == [0.25, 0.5]
    assert captured["base"] == "http://embed.test:8503"


def test_embed_http_flow_falls_back_through_real_provider_adapter(monkeypatch: pytest.MonkeyPatch):
    _configure(_catalog(), _policies(["local", "openrouter.paid", "openai"]))
    server.write_env({
        "LLM_EMBED_API_BASE": "http://local.test:8503",
        "OPENROUTER_API_BASE": "https://router.test/api/v1",
        "OPENROUTER_API_KEY": "test-placeholder",
    })
    monkeypatch.setattr(server, "_maybe_refresh_openrouter_catalog", lambda *_args, **_kwargs: {})
    calls: list[str] = []

    class Response:
        def __init__(self, url: str, status_code: int, body: dict):
            self.url = url
            self.status_code = status_code
            self.ok = status_code < 400
            self._body = body
            self.text = str(body)

        def json(self):
            return self._body

        def raise_for_status(self):
            if not self.ok:
                error = requests.HTTPError(f"HTTP {self.status_code}")
                error.response = self
                raise error

    def post(url, **_kwargs):
        calls.append(url)
        if url.startswith("http://local.test"):
            return Response(url, 503, {"error": {"message": "local unavailable"}})
        return Response(
            url,
            200,
            {
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    monkeypatch.setattr(requests, "post", post)
    with TestClient(server.app) as client:
        response = client.post("/router/embed", json={"input": ["hello"]})
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "openrouter"
    assert calls == [
        "http://local.test:8503/v1/embeddings",
        "https://router.test/api/v1/embeddings",
    ]


def test_local_dispatch_routes_by_alias_but_sends_actual_served_model(monkeypatch: pytest.MonkeyPatch):
    catalog = _catalog()
    _configure(catalog, _policies(["local"]))
    server.write_env({"LLM_CHAT_API_BASE": "http://local.test:8500"})
    captured = {}

    class Response:
        status_code = 200
        ok = True

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

        def raise_for_status(self):
            return None

    def post(url, **kwargs):
        captured.update({"url": url, "payload": kwargs["json"]})
        return Response()

    monkeypatch.setattr(requests, "post", post)
    result = server._dispatch_provider_chat(
        "local",
        server.read_env(),
        {"model": "chat_active_model", "messages": [{"role": "user", "content": "hello"}]},
        provider_models=catalog,
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert captured["url"] == "http://local.test:8500/v1/chat/completions"
    assert captured["payload"]["model"] == "chat-checkpoint"


def test_chat_endpoint_records_local_error_then_falls_back_to_openai(monkeypatch: pytest.MonkeyPatch):
    _configure(_catalog(), _policies(["local", "openai"]))
    monkeypatch.setattr(server, "_maybe_refresh_openrouter_catalog", lambda *_args, **_kwargs: {})

    def dispatch(provider, env, payload, provider_models=None):
        if provider == "local":
            raise RuntimeError("local HTTP 503 unavailable")
        return {
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "fallback"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }

    monkeypatch.setattr(server, "_dispatch_provider_chat", dispatch)
    with TestClient(server.app) as client:
        response = client.post("/router/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "openai"
    assert body["choices"][0]["message"]["content"] == "fallback"
    assert [attempt["result"] for attempt in body["routing"]["attempt_trace"]] == ["error", "selected"]


def test_chat_skips_known_unavailable_local_lane_and_marks_remote_fallback(monkeypatch: pytest.MonkeyPatch):
    _configure(_catalog(), _policies(["local", "openai"]))
    monkeypatch.setattr(server, "_maybe_refresh_openrouter_catalog", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        server,
        "_known_local_lane_error",
        lambda provider, *_args, **_kwargs: (
            {
                "type": "local_lane_unavailable",
                "message": "chat lane has a static configuration error",
                "retryable": False,
                "failure_type": "configuration_error",
                "incident_key": "lane:chat:configuration_error:tgw:chat-checkpoint",
            }
            if provider == "local"
            else None
        ),
    )
    dispatched: list[str] = []

    def dispatch(provider, env, payload, provider_models=None):
        dispatched.append(provider)
        assert provider == "openai"
        return {
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "fallback"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }

    monkeypatch.setattr(server, "_dispatch_provider_chat", dispatch)
    with TestClient(server.app) as client:
        response = client.post("/router/chat", json={"messages": [{"role": "user", "content": "hello"}]})

    assert response.status_code == 200, response.text
    routing = response.json()["routing"]
    assert dispatched == ["openai"]
    assert [row["reason_code"] for row in routing["attempt_trace"]] == ["local_lane_unavailable", "selected"]
    assert routing["fallback_summary"]["service_state"] == "remote_fallback"
    assert routing["fallback_summary"]["degraded"] is True


def test_chat_endpoint_accepts_null_usage_from_local_backend(monkeypatch: pytest.MonkeyPatch):
    _configure(_catalog(), _policies(["local"]))
    monkeypatch.setattr(server, "_maybe_refresh_openrouter_catalog", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(server, "_dispatch_provider_chat", lambda *_args, **_kwargs: {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": None,
    })

    with TestClient(server.app) as client:
        response = client.post("/router/chat", json={"messages": [{"role": "user", "content": "hello"}]})

    assert response.status_code == 200, response.text
    assert response.json()["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_completion_endpoint_falls_back_after_provider_error(monkeypatch: pytest.MonkeyPatch):
    _configure(_catalog(), _policies(["openrouter.paid", "openai"]))
    monkeypatch.setattr(server, "_maybe_refresh_openrouter_catalog", lambda *_args, **_kwargs: {})

    def dispatch(provider, env, payload, provider_models=None):
        if provider == "openrouter":
            raise RuntimeError("HTTP 429 rate limited")
        return {
            "choices": [{"index": 0, "text": "completed", "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(server, "_dispatch_provider_completions", dispatch)
    with TestClient(server.app) as client:
        response = client.post("/router/completions", json={"prompt": "hello"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "openai"
    assert body["choices"][0]["text"] == "completed"
    assert body["routing"]["attempt_errors"][0]["error"]["type"] == "rate_limited"


def test_governance_write_and_rollback_round_trip_through_http():
    catalog = _catalog()
    _configure(catalog, _policies(["local", "openai"]))
    with TestClient(server.app) as client:
        current = client.get("/providers/models").json()
        changed = copy.deepcopy(current["models"])
        changed["local"]["slots"][0]["label"] = "Changed label"
        written = client.put(
            "/providers/models",
            json={
                "document": changed,
                "expected_version": current["version"],
                "reason": "integration test",
                "actor": "pytest",
            },
        )
        assert written.status_code == 200, written.text
        rolled_back = client.post(
            "/providers/models/rollback",
            json={
                "expected_version": written.json()["version"],
                "reason": "integration rollback",
                "actor": "pytest",
            },
        )
        assert rolled_back.status_code == 200, rolled_back.text
        final = client.get("/providers/models").json()["models"]
    assert final["local"]["slots"][0]["label"] == "Chat"
