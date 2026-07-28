from __future__ import annotations

import copy
import json
import time

import requests

from llm_manager import server
from llm_manager.evaluation.schemas import EvalCase, EvalJudgeConfig, LocalEvalRequest
from llm_manager.providers.openrouter import OpenRouterProviderAdapter
from llm_manager.router.contracts import RouterChatRequest


def test_deterministic_assertions_and_schema_are_hard_gates():
    case = EvalCase(
        case_id="structured",
        prompt="return json",
        expected_contains=["Dune"],
        response_json_schema={
            "type": "object",
            "properties": {"title": {"type": "string"}, "year": {"type": "integer"}},
            "required": ["title", "year"],
            "additionalProperties": False,
        },
    )
    passed, assertion_pass = server._run_case_assertions(
        '{"title":"Dune","year":1965}',
        {},
        case,
        latency_ms=10,
        estimated_cost_usd=0,
    )
    assert assertion_pass is True
    assert all(item["passed"] for item in passed)

    _, assertion_pass = server._run_case_assertions(
        '{"title":"Dune","year":"1965"}',
        {},
        case,
        latency_ms=10,
        estimated_cost_usd=0,
    )
    assert assertion_pass is False


def test_free_discovery_candidates_have_zero_run_ceiling():
    req = LocalEvalRequest(
        suite_name="free",
        cases=[{"case_id": "one", "prompt": "test"}],
        repetitions=3,
    )
    candidates = [{
        "provider": "openrouter",
        "lane": "openrouter_free",
        "model": "vendor/free:free",
    }]
    assert server._estimate_eval_run_ceiling(req, candidates, {}) == 0.0


def test_evaluation_provider_deadline_is_wall_clock(monkeypatch):
    def trickle(*_args, **_kwargs):
        time.sleep(1.2)
        return {"choices": []}

    monkeypatch.setattr(server, "_dispatch_provider_chat", trickle)
    started = time.monotonic()
    try:
        server._dispatch_evaluation_chat_with_deadline(
            "openrouter",
            {},
            {},
            provider_models={},
            timeout_seconds=1,
            run_id="deadline-test",
        )
    except requests.Timeout as exc:
        assert "wall-clock deadline" in str(exc)
    else:  # pragma: no cover - the assertion above is the expected path
        raise AssertionError("provider call did not honor its wall-clock deadline")
    assert time.monotonic() - started < 1.15


def test_schedule_initialization_is_persistent():
    scheduler = server._initialize_evaluation_schedule()
    assert scheduler["enabled"] is True
    assert set(scheduler["jobs"]) == {"daily_health", "weekly_discovery", "biweekly_benchmark"}
    assert all(row["next_run_ts"] for row in scheduler["jobs"].values())

    scheduler_again = server._initialize_evaluation_schedule()
    assert {
        name: row["next_run_ts"] for name, row in scheduler_again["jobs"].items()
    } == {
        name: row["next_run_ts"] for name, row in scheduler["jobs"].items()
    }


def test_new_running_job_is_not_misclassified_as_stale():
    now = server._utc_now_iso()
    state = server.read_provider_runtime_state()
    state["evaluation_queue"] = [{
        "run_id": "fresh",
        "priority": "evaluation",
        "status": "running",
        "started_ts": now,
        "request": {},
    }]
    state["evaluation_runs"] = {
        "fresh": {"run_id": "fresh", "status": "running", "started_ts": now},
    }
    server.write_provider_runtime_state(state)

    assert server._claim_next_eval_job() is None
    persisted = server.read_provider_runtime_state()
    assert persisted["evaluation_runs"]["fresh"]["status"] == "running"


def test_cancelling_job_holds_capacity_and_is_visible_as_active():
    now = server._utc_now_iso()
    state = server.read_provider_runtime_state()
    state["evaluation_queue"] = [
        {
            "run_id": "stopping",
            "priority": "evaluation",
            "status": "cancelling",
            "started_ts": now,
            "heartbeat_ts": now,
            "request": {},
        },
        {
            "run_id": "next",
            "priority": "evaluation",
            "status": "queued",
            "enqueued_ts": now,
            "request": {},
        },
    ]
    state["evaluation_runs"] = {
        "stopping": {"run_id": "stopping", "status": "cancelling"},
        "next": {"run_id": "next", "status": "queued"},
    }
    server.write_provider_runtime_state(state)

    assert server._claim_next_eval_job() is None
    queue_state = server.router_evaluation_queue_state(limit=100)
    assert queue_state["running_count"] == 1
    assert queue_state["running"][0]["status"] == "cancelling"


def test_evidence_promotion_builds_default_and_fallback_chain():
    state = server.read_provider_runtime_state()
    models = ["vendor/best:free", "vendor/fallback:free", "vendor/slow:free"]
    state["openrouter_catalog_cache"] = {
        "fetched_ts": server._utc_now_iso(),
        "free_ids": models,
        "models": [
            {"id": model, "is_free": True, "capabilities": ["chat"]}
            for model in models
        ],
    }
    state["openrouter_free_candidates"] = {
        "candidates": [
            {
                "id": model,
                "is_free": True,
                "capabilities": ["chat"],
                "activation_eligible": True,
                "promotion_state": "candidate",
            }
            for model in models
        ],
        "active_ids": [],
    }
    server.write_provider_runtime_state(state)

    rows = []
    for model, judge_score in (("vendor/best:free", 0.95), ("vendor/fallback:free", 0.80)):
        for index in range(8):
            rows.append({
                "provider": "openrouter",
                "lane": "openrouter_free",
                "model": model,
                "tags": ["general"],
                "ok": True,
                "case_pass": True,
                "judge_score": judge_score,
                "latency_ms": 100 + index,
            })
    for index in range(8):
        rows.append({
            "provider": "openrouter",
            "lane": "openrouter_free",
            "model": "vendor/slow:free",
            "tags": ["general"],
            "ok": True,
            "case_pass": True,
            "judge_score": 0.99,
            "latency_ms": 31000 + index,
        })
    result = server._promote_from_evaluation_run({
        "run_id": "promotion-run",
        "suite_name": "benchmark",
        "suite_version": "1",
        "metadata": {"promotion_eligible": True},
        "results": rows,
    })
    assert result["active_ids"][:2] == models[:2]
    persisted = server.read_provider_runtime_state()
    general = persisted["evaluation_promotions"]["profiles"]["general"]
    assert general["primary_model"] == models[0]
    assert general["fallback_models"][0] == models[1]
    assert models[2] not in general["candidate_chain"]
    assert persisted["openrouter_free_candidates"]["activation_mode"] == "automated_evaluation"


def test_daily_reconciliation_promotes_available_fallback():
    state = server.read_provider_runtime_state()
    state["openrouter_catalog_cache"] = {
        "free_ids": ["vendor/fallback:free"],
        "models": [],
    }
    state["evaluation_promotions"] = {
        "profiles": {
            "general": {
                "primary_model": "vendor/missing:free",
                "fallback_models": ["vendor/fallback:free"],
                "candidate_chain": ["vendor/missing:free", "vendor/fallback:free"],
            },
        },
        "history": [],
    }
    state["openrouter_free_candidates"] = {"active_ids": ["vendor/missing:free", "vendor/fallback:free"]}
    server.write_provider_runtime_state(state)

    result = server._reconcile_evaluation_promotions()
    assert result["active_ids"] == ["vendor/fallback:free"]
    general = server.read_provider_runtime_state()["evaluation_promotions"]["profiles"]["general"]
    assert general["primary_model"] == "vendor/fallback:free"


def test_free_rotation_flag_immediately_updates_discovered_pool():
    model_id = "vendor/slow:free"
    state = server.read_provider_runtime_state()
    state["openrouter_free_candidates"] = {
        "candidates": [{
            "id": model_id,
            "activation_eligible": True,
            "promotion_state": "active",
        }],
        "active_ids": [model_id],
    }
    server.write_provider_runtime_state(state)

    server.providers_model_flags_update(server.ProviderModelFlagsReq(
        model_key=f"openrouter:{model_id}",
        exclude_from_free_rotation=True,
        reason="latency threshold exceeded",
        actor="test",
    ))

    persisted = server.read_provider_runtime_state()["openrouter_free_candidates"]
    assert persisted["active_ids"] == []
    assert persisted["candidates"][0]["promotion_state"] == "quarantined"
    assert persisted["candidates"][0]["exclude_from_free_rotation"] is True


def test_promoted_discovered_model_precedes_static_free_catalog():
    promoted = "vendor/promoted:free"
    coding_promoted = "vendor/coding:free"
    state = server.read_provider_runtime_state()
    state["openrouter_catalog_cache"] = {
        "free_ids": [promoted, coding_promoted, "vendor/curated:free"],
        "models": [
            {"id": promoted, "is_free": True, "capabilities": ["chat"]},
            {"id": coding_promoted, "is_free": True, "capabilities": ["chat"]},
            {"id": "vendor/curated:free", "is_free": True, "capabilities": ["chat"]},
        ],
    }
    state["evaluation_promotions"] = {
        "profiles": {
            "general": {
                "primary_model": promoted,
                "candidate_chain": [promoted],
            },
            "coding": {
                "primary_model": coding_promoted,
                "candidate_chain": [coding_promoted],
            },
        },
    }
    server.write_provider_runtime_state(state)

    provider, model = server._pick_catalog_model(
        "openrouter.free",
        {
            "openrouter": {
                "free": [{
                    "id": "vendor/curated:free",
                    "enabled": True,
                    "priority": 1,
                    "capabilities": ["chat"],
                }],
            },
        },
        RouterChatRequest(messages=[{"role": "user", "content": "test"}]),
        policies={"openrouter": {"enforce_upstream_free_status": False}},
    )
    assert provider == "openrouter"
    assert model == promoted

    _, coding_model = server._pick_catalog_model(
        "openrouter.free",
        {
            "openrouter": {
                "free": [{
                    "id": "vendor/curated:free",
                    "enabled": True,
                    "priority": 1,
                    "capabilities": ["chat"],
                }],
            },
        },
        RouterChatRequest(
            messages=[{"role": "user", "content": "write code"}],
            model_preferences={"preferred_model_tags": ["coding"]},
        ),
        policies={"openrouter": {"enforce_upstream_free_status": False}},
    )
    assert coding_model == coding_promoted


def test_dynamic_ranking_skips_capability_incompatible_lane():
    policies = copy.deepcopy(server.DEFAULT_PROVIDER_POLICIES)
    provider_models = {
        "local": {
            "slots": [{
                "id": "chat",
                "enabled": True,
                "capabilities": ["chat", "completions"],
            }],
        },
        "openrouter": {
            "free": [{
                "id": "vendor/reasoning:free",
                "enabled": True,
                "capabilities": ["chat", "reasoning"],
            }],
        },
    }
    request = RouterChatRequest(
        messages=[{"role": "user", "content": "reason"}],
        model_preferences={"preferred_model_tags": ["reasoning"]},
    )

    ranked, rows = server._rank_candidate_chain(
        ["local", "openrouter.free"],
        request,
        provider_models,
        policies,
        server._dynamic_ranking_policy(policies),
    )
    assert ranked == ["openrouter.free"]
    assert next(row for row in rows if row["lane"] == "local")["available"] is False


def test_judge_stage_uses_configured_fallback(monkeypatch):
    calls = []

    def execute(*, judge_ref, **_kwargs):
        calls.append(judge_ref)
        if judge_ref.endswith("gpt-5.6-luna"):
            return {"status": "error", "error": {"type": "model_unavailable"}}, 0.0
        return {
            "status": "completed",
            "judge_ref": judge_ref,
            "score": 0.9,
            "pass": True,
        }, 0.001

    monkeypatch.setattr(server, "_execute_eval_judgment", execute)
    config = EvalJudgeConfig(
        enabled=True,
        preliminary_fallback_models=["openrouter.paid:deepseek/deepseek-v4-flash"],
        preliminary_top_fraction=1,
        disagreement_threshold=1,
    )
    rows = [
        {
            "run_id": "run",
            "case_id": "case",
            "variant_id": "v",
            "repetition_index": repetition_index,
            "candidate_ref": "openrouter.free:vendor/free:free",
            "model": "vendor/free:free",
            "ok": True,
            "assertion_pass": True,
            "plugin_score_max": 0,
            "case_pass": True,
        }
        for repetition_index in range(2)
    ]
    result = server._adjudicate_evaluation_rows(
        rows,
        [EvalCase(case_id="case", prompt="test", rubric="correct")],
        config,
        env={},
        provider_models={},
        policies={},
    )
    assert calls[:2] == [
        "openrouter.paid:openai/gpt-5.6-luna",
        "openrouter.paid:deepseek/deepseek-v4-flash",
    ]
    assert calls.count("openrouter.paid:openai/gpt-5.6-luna") == 1
    assert calls.count("openrouter.paid:deepseek/deepseek-v4-flash") == 2
    assert rows[0]["judge_preliminary"]["judge_ref"].endswith("deepseek-v4-flash")
    assert result["judgment_count"] >= 1


def test_judge_payload_avoids_temperature_for_reasoning_models():
    row = {
        "run_id": "run",
        "case_id": "case",
        "variant_id": "v",
        "repetition_index": 0,
        "model": "candidate",
        "prompt": "test",
        "output_text": "answer",
    }
    payload = server._judge_payload(
        row,
        EvalCase(case_id="case", prompt="test", rubric="correct"),
        "openai/gpt-5.6-luna",
        EvalJudgeConfig(),
    )
    assert "temperature" not in payload
    assert payload["reasoning"]["effort"] == "medium"


def test_openrouter_error_preserves_nested_provider_detail():
    response = requests.Response()
    response.status_code = 400
    response._content = json.dumps({  # noqa: SLF001 - requests exposes no public response-body builder
        "error": {
            "message": "Provider returned error",
            "code": 400,
            "metadata": {
                "raw": json.dumps({
                    "error": {
                        "message": "Invalid JSON schema",
                        "type": "invalid_request_error",
                        "code": "invalid_json_schema",
                    },
                }),
            },
        },
    }).encode()
    error = requests.HTTPError(response=response)

    normalized = OpenRouterProviderAdapter().normalize_error(error)
    assert normalized["type"] == "invalid_request"
    assert normalized["message"] == "Invalid JSON schema"
    assert normalized["provider_code"] == "invalid_json_schema"
