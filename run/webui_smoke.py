#!/usr/bin/env python3
"""In-process smoke checks for critical WebUI control paths.

Covers:
1) switch + bounce path
2) evaluation suite rerun path
3) OpenRouter catalog refresh path
4) budget warning/exceeded visibility path
"""

from __future__ import annotations

import copy
import json
import os
import sys
from contextlib import ExitStack, contextmanager
from typing import Any, Dict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
API_DIR = os.path.join(ROOT, "api")
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from fastapi.testclient import TestClient  # noqa: E402
from api import server  # noqa: E402


@contextmanager
def patched(obj: Any, name: str, value: Any):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


def check_switch_and_bounce(client: TestClient) -> Dict[str, Any]:
    calls: Dict[str, Any] = {}

    def fake_switch_model(mode: str, model_dir: str, bounce: bool = True, backend: str | None = None):
        calls.update({"mode": mode, "model_dir": model_dir, "bounce": bounce, "backend": backend})
        return {"ok": True, "mode": mode, "model_dir": model_dir, "bounced": bounce}

    with patched(server, "switch_model", fake_switch_model):
        resp = client.post(
            "/switch",
            json={"mode": "chat", "model_dir": "chat_active_model", "bounce": True},
        )

    body = resp.json()
    assert resp.status_code == 200, f"switch+bounce expected 200, got {resp.status_code}: {body}"
    assert calls.get("mode") == "chat", f"switch mode mismatch: {calls}"
    assert calls.get("model_dir") == "chat_active_model", f"switch model mismatch: {calls}"
    assert calls.get("bounce") is True, f"bounce flag mismatch: {calls}"
    assert body.get("ok") is True, f"switch body did not return ok=true: {body}"

    return {"http": resp.status_code, "ok": True, "calls": calls}


def check_eval_rerun(client: TestClient) -> Dict[str, Any]:
    state = {
        "evaluation_suites": {
            "smoke_suite:1": {
                "suite_name": "smoke_suite",
                "suite_version": "1",
                "target_mode": "chat",
                "candidate_models": ["chat_active_model"],
                "variants": [
                    {
                        "variant_id": "default",
                        "temperature": 0.2,
                        "top_p": 0.9,
                        "max_tokens": 64,
                        "stop": [],
                        "system": None,
                    }
                ],
                "cases": [
                    {
                        "case_id": "c1",
                        "prompt": "hello",
                        "expected_contains": [],
                        "tags": ["smoke"],
                        "system": None,
                        "scoring_plugins": [],
                    }
                ],
                "metadata": {},
            }
        }
    }

    writes: list[Dict[str, Any]] = []

    def fake_read_state() -> Dict[str, Any]:
        return copy.deepcopy(state)

    def fake_write_state(new_state: Dict[str, Any]) -> None:
        writes.append(copy.deepcopy(new_state))

    def fake_enqueue(_req: Any, priority: str = "evaluation"):
        return "run-smoke-123", 1, "2026-03-29T00:00:00Z"

    with ExitStack() as stack:
        stack.enter_context(patched(server, "read_provider_runtime_state", fake_read_state))
        stack.enter_context(patched(server, "write_provider_runtime_state", fake_write_state))
        stack.enter_context(patched(server, "_enqueue_local_evaluation", fake_enqueue))

        resp = client.post(
            "/router/evaluation-suites/smoke_suite/1/rerun",
            json={"async_run": True, "priority": "evaluation"},
        )

    body = resp.json()
    assert resp.status_code == 200, f"evaluation rerun expected 200, got {resp.status_code}: {body}"
    assert body.get("ok") is True, f"evaluation rerun expected ok=true: {body}"
    assert body.get("mode") == "async", f"evaluation rerun expected async mode: {body}"
    assert body.get("run_id") == "run-smoke-123", f"evaluation rerun run_id mismatch: {body}"

    return {"http": resp.status_code, "ok": True, "writes": len(writes), "run_id": body.get("run_id")}


def check_openrouter_refresh(client: TestClient) -> Dict[str, Any]:
    def fake_refresh(_env: Dict[str, str], include_rankings: bool = False):
        return {
            "fetched_ts": "2026-03-29T00:00:00Z",
            "count": 2,
            "free_ids": ["openrouter/model-a:free"],
            "models": [{"id": "openrouter/model-a:free"}, {"id": "openrouter/model-b"}],
            "rankings": [] if include_rankings else [],
            "error": None,
        }

    with patched(server, "_refresh_openrouter_catalog", fake_refresh):
        resp = client.post("/providers/openrouter/refresh")

    body = resp.json()
    assert resp.status_code == 200, f"openrouter refresh expected 200, got {resp.status_code}: {body}"
    assert body.get("ok") is True, f"openrouter refresh expected ok=true: {body}"
    catalog = body.get("openrouter_catalog") or {}
    assert catalog.get("count") == 2, f"openrouter refresh expected count=2: {catalog}"

    return {"http": resp.status_code, "ok": True, "count": catalog.get("count")}


def check_budget_visibility(client: TestClient) -> Dict[str, Any]:
    def fake_policies() -> Dict[str, Any]:
        return {"budget": {"warn_threshold_pct": 0.8}}

    def fake_budget_warn(_policies: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "daily_limit_usd": 10.0,
            "monthly_limit_usd": 100.0,
            "day_total_usd": 8.2,
            "month_total_usd": 50.0,
        }

    def fake_budget_exceeded(_policies: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "daily_limit_usd": 10.0,
            "monthly_limit_usd": 100.0,
            "day_total_usd": 11.5,
            "month_total_usd": 120.0,
        }

    with ExitStack() as stack:
        stack.enter_context(patched(server, "read_provider_policies", fake_policies))
        stack.enter_context(patched(server, "_budget_snapshot", fake_budget_warn))
        warn_resp = client.get("/router/budget-state")

    warn_body = warn_resp.json()
    assert warn_resp.status_code == 200, f"budget warn expected 200, got {warn_resp.status_code}: {warn_body}"
    assert warn_body.get("daily_warn") is True, f"budget warn expected daily_warn=true: {warn_body}"
    assert warn_body.get("daily_exceeded") is False, f"budget warn expected daily_exceeded=false: {warn_body}"

    with ExitStack() as stack:
        stack.enter_context(patched(server, "read_provider_policies", fake_policies))
        stack.enter_context(patched(server, "_budget_snapshot", fake_budget_exceeded))
        exceeded_resp = client.get("/router/budget-state")

    exceeded_body = exceeded_resp.json()
    assert exceeded_resp.status_code == 200, (
        f"budget exceeded expected 200, got {exceeded_resp.status_code}: {exceeded_body}"
    )
    assert exceeded_body.get("daily_exceeded") is True, (
        f"budget exceeded expected daily_exceeded=true: {exceeded_body}"
    )
    assert exceeded_body.get("monthly_exceeded") is True, (
        f"budget exceeded expected monthly_exceeded=true: {exceeded_body}"
    )

    return {
        "warn_http": warn_resp.status_code,
        "exceeded_http": exceeded_resp.status_code,
        "ok": True,
    }


def main() -> int:
    client = TestClient(server.app)

    summary: Dict[str, Any] = {}
    summary["switch_bounce"] = check_switch_and_bounce(client)
    summary["eval_rerun"] = check_eval_rerun(client)
    summary["openrouter_refresh"] = check_openrouter_refresh(client)
    summary["budget_visibility"] = check_budget_visibility(client)

    print(json.dumps({"ok": True, "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
