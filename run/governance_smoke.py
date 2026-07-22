#!/usr/bin/env python3
"""In-process governance contract smoke checks."""

from __future__ import annotations

import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient  # noqa: E402
from llm_manager import server  # noqa: E402


def main() -> int:
    client = TestClient(server.app)

    policies = client.get("/providers/policies").json()
    pol_doc = copy.deepcopy(policies["policies"])
    pol_ver = policies["version"]
    pol_validate = client.put("/providers/policies", json={
        "document": pol_doc,
        "expected_version": pol_ver,
        "validate_only": True,
        "reason": "validate policies smoke",
        "actor": "smoke",
    })
    assert pol_validate.status_code == 200, pol_validate.text

    models = client.get("/providers/models").json()
    original_doc = copy.deepcopy(models["models"])
    original_version = models["version"]

    test_doc = copy.deepcopy(original_doc)
    test_doc["__gov_smoke_marker"] = {"ts": "2026-03-29T00:00:00Z"}

    validate = client.put("/providers/models", json={
        "document": test_doc,
        "expected_version": original_version,
        "validate_only": True,
        "reason": "validate smoke",
        "actor": "smoke",
    })
    assert validate.status_code == 200, validate.text

    apply = client.put("/providers/models", json={
        "document": test_doc,
        "expected_version": original_version,
        "validate_only": False,
        "reason": "apply smoke",
        "actor": "smoke",
    })
    assert apply.status_code == 200, apply.text

    after_apply = client.get("/providers/models").json()
    applied_version = after_apply["version"]
    assert applied_version != original_version

    stale = client.put("/providers/models", json={
        "document": test_doc,
        "expected_version": original_version,
        "validate_only": False,
        "reason": "stale smoke",
        "actor": "smoke",
    })
    assert stale.status_code == 409, stale.text

    rollback = client.post("/providers/models/rollback", json={
        "expected_version": applied_version,
        "reason": "rollback smoke",
        "actor": "smoke",
    })
    assert rollback.status_code == 200, rollback.text

    final = client.get("/providers/models").json()
    assert final["version"] == original_version
    assert final["models"] == original_doc

    state = client.get("/providers/state").json().get("state", {})
    audit = state.get("governance_audit", []) if isinstance(state.get("governance_audit"), list) else []
    tail = [a.get("action") for a in audit[-3:]]

    flags = client.post("/providers/state/provider-model-flags", json={
        "model_key": "openrouter:test-model",
        "disabled_until_manual_review": True,
        "exclude_from_free_rotation": True,
        "reason": "flags smoke",
        "actor": "smoke",
    })
    assert flags.status_code == 200, flags.text

    print(json.dumps({
        "ok": True,
        "policies_validate": pol_validate.status_code,
        "original_version": original_version,
        "applied_version": applied_version,
        "final_version": final["version"],
        "audit_tail_actions": tail,
        "flags_update": flags.status_code,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
