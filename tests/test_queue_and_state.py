from __future__ import annotations

import os
import threading
import time

from api import server


def _queue_policies(*, rpm: int = 1, wait_ms: int = 500) -> dict:
    return {
        "openrouter": {
            "free_rate_limit_rpm": rpm,
            "max_queue_depth": 10,
            "max_queue_wait_ms": wait_ms,
            "queue_behavior": "wait",
        }
    }


def test_wait_queue_blocks_then_acquires_capacity_without_persisting_metadata():
    policies = _queue_policies()
    state = server.read_provider_runtime_state()
    state["provider_rate_limits"]["openrouter_free"].update({
        "rpm_limit": 1,
        "window_seconds": 60,
        "window_start": int(time.time()),
        "request_count": 1,
    })
    server.write_provider_runtime_state(state)

    def release_capacity():
        time.sleep(0.12)
        with server._provider_state_transaction():
            current = server.read_provider_runtime_state()
            current["provider_rate_limits"]["openrouter_free"].update({"window_start": 0, "request_count": 0})
            server.write_provider_runtime_state(current)

    releaser = threading.Thread(target=release_capacity)
    releaser.start()
    action, detail = server._handle_free_tier_overflow(
        "chat",
        "free_first",
        False,
        {"priority": "interactive", "api_key": "sk-test-placeholder"},
        {"request_count": 1},
        policies,
    )
    releaser.join(timeout=2)

    assert action == "acquired"
    assert detail["queue"]["state"] == "acquired"
    stored = server.read_provider_runtime_state()
    assert stored["provider_request_queue"] == []
    assert "sk-test-placeholder" not in server.PROVIDER_STATE_PATH.read_text()


def test_wait_queue_is_priority_ordered():
    policies = _queue_policies(rpm=5)
    ok_batch, batch = server._enqueue_free_tier_request("embed", "free_first", {"priority": "batch"}, policies)
    ok_interactive, interactive = server._enqueue_free_tier_request("chat", "free_first", {"priority": "interactive"}, policies)
    assert ok_batch and ok_interactive

    status, detail = server._claim_queued_free_tier_request(batch["entry_id"], policies)
    assert status == "waiting"
    assert detail["position"] == 2
    assert server._claim_queued_free_tier_request(interactive["entry_id"], policies)[0] == "acquired"
    assert server._claim_queued_free_tier_request(batch["entry_id"], policies)[0] == "acquired"


def test_wait_queue_times_out_and_removes_entry():
    policies = _queue_policies(wait_ms=30)
    state = server.read_provider_runtime_state()
    state["provider_rate_limits"]["openrouter_free"].update({
        "rpm_limit": 1,
        "window_seconds": 60,
        "window_start": int(time.time()),
        "request_count": 1,
    })
    server.write_provider_runtime_state(state)
    action, detail = server._handle_free_tier_overflow(
        "chat", "free_first", False, {}, {"request_count": 1}, policies,
    )
    assert action == "break"
    assert detail["type"] == "queue_timeout"
    assert server.read_provider_runtime_state()["provider_request_queue"] == []


def test_concurrent_state_updates_do_not_lose_spend_records():
    count = 24
    errors: list[Exception] = []

    def record(index: int):
        try:
            server._record_spend("openai", "openai", "model", 0.01, str(index), "paid_first", policies={})
        except Exception as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    threads = [threading.Thread(target=record, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    state = server.read_provider_runtime_state()
    assert len(state["spend_logs"]) == count
    assert round(state["budget_state"]["lifetime_total_usd"], 2) == 0.24
    assert os.stat(server.PROVIDER_STATE_PATH).st_mode & 0o777 == 0o600
