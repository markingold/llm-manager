from __future__ import annotations

import pytest
from fastapi import HTTPException

from api import server
from api.router.contracts import RouterChatRequest, RouterModelPreferences


def _catalog() -> dict:
    return {
        "local": {
            "slots": [
                {"id": "chat", "enabled": True, "capabilities": ["chat"]},
                {"id": "small", "enabled": True, "capabilities": ["chat", "structured_outputs"]},
            ]
        },
        "openrouter": {
            "free": [{"id": "vendor/free", "enabled": True, "supports_tools": True}],
            "paid": [{"id": "vendor/paid", "enabled": True, "supports_tools": True}],
        },
        "openai": {
            "allowed": [{
                "id": "approved-model",
                "enabled": True,
                "supports_tools": True,
                "input_cost_usd_per_1k": 2.0,
                "output_cost_usd_per_1k": 4.0,
            }]
        },
    }


def test_explicit_model_must_exist_in_enabled_catalog_and_candidate_chain():
    req = RouterChatRequest(model_preferences=RouterModelPreferences(preferred_model="made-up/model"))
    with pytest.raises(HTTPException) as exc:
        server._constrain_chain_to_preferred_model(req, ["local", "openrouter.paid", "openai"], _catalog(), {})
    assert exc.value.status_code == 422

    req.model_preferences.preferred_model = "approved-model"
    assert server._constrain_chain_to_preferred_model(req, ["local", "openai"], _catalog(), {}) == ["openai"]
    assert server._pick_catalog_model("openai", _catalog(), req, policies={}) == ("openai", "approved-model")


def test_explicit_model_cannot_jump_from_free_to_paid_lane():
    req = RouterChatRequest(model_preferences=RouterModelPreferences(preferred_model="vendor/free"))
    with pytest.raises(HTTPException):
        server._constrain_chain_to_preferred_model(req, ["openrouter.paid"], _catalog(), {})


def test_capability_routing_fails_closed_for_unknown_values():
    assert server._row_supports_requirements({}, {"tools": True}) is False
    assert server._row_supports_requirements({"supports_tools": False}, {"tools": True}) is False
    assert server._row_supports_requirements({"supports_tools": True}, {"tools": True}) is True

    req = RouterChatRequest(messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    with pytest.raises(HTTPException) as exc:
        server._pick_catalog_model("local", _catalog(), req, policies={})
    assert exc.value.status_code == 422


def test_budget_guardrail_applies_to_explicit_models():
    policies = {
        "budget": {
            "providers": {"openai": True},
            "hard_fail_on_budget_exceeded": True,
            "per_request_usd_limit": 0.01,
        }
    }
    with pytest.raises(HTTPException) as exc:
        server._enforce_budget_guardrail(
            policies,
            "openai",
            "openai",
            "approved-model",
            _catalog(),
            100,
        )
    assert exc.value.status_code == 429


def test_sensitive_request_metadata_is_redacted_before_persistence():
    decision = {
        "request_id": "abc",
        "metadata": {
            "api_key": "sk-test-placeholder",
            "nested": {"token": "hf_abcdefghijklmnop", "safe": "kept"},
        },
    }
    server._append_router_decision(decision, policies={})
    stored = server.read_provider_runtime_state()["request_logs"][-1]
    assert stored["metadata"]["api_key"] == "[REDACTED]"
    assert stored["metadata"]["nested"]["token"] == "[REDACTED]"
    assert stored["metadata"]["nested"]["safe"] == "kept"


def test_governance_version_check_reloads_document_inside_transaction():
    stale = {"local": {"slots": []}, "openrouter": {"free": [], "paid": []}, "openai": {"allowed": []}}
    current = {**stale, "revision": 2}
    server._write_json(server.PROVIDER_MODELS_PATH, current)

    with pytest.raises(HTTPException) as exc:
        server._governance_apply(
            "models",
            server.PROVIDER_MODELS_PATH,
            stale,
            stale,
            server._doc_version(stale),
            "test",
            "concurrency regression",
            False,
        )
    assert exc.value.status_code == 409
    assert server.read_provider_models() == current
