#!/usr/bin/env python3
"""In-process smoke checks for TGW WebUI controls.

Covers:
1) engine status exposes tgw_webui payload
2) config update for TGW WebUI launch settings
3) standalone TGW WebUI service start/stop actions
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
from fastapi.testclient import TestClient  # noqa: E402
from llm_manager import server  # noqa: E402


@contextmanager
def patched(obj: Any, name: str, value: Any):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


def check_status_payload(client: TestClient) -> Dict[str, Any]:
    resp = client.get("/engines/status")
    body = resp.json()
    assert resp.status_code == 200, f"status expected 200, got {resp.status_code}: {body}"
    assert isinstance(body.get("tgw_webui"), dict), f"status missing top-level tgw_webui: {body}"
    return {"http": resp.status_code, "ok": True}


def check_config(client: TestClient, port: int, bind_host: str) -> Dict[str, Any]:
    resp = client.post("/engines/tgw-webui/config", json={"port": port, "bind_host": bind_host})
    body = resp.json()
    assert resp.status_code == 200, f"config expected 200, got {resp.status_code}: {body}"
    assert body.get("ok") is True, f"config expected ok=true: {body}"
    tgw = body.get("tgw_webui") or {}
    assert int(tgw.get("port") or 0) == int(port), f"config port mismatch: {tgw}"
    assert tgw.get("bind_host") == bind_host, f"config bind_host mismatch: {tgw}"
    return {"http": resp.status_code, "ok": True, "port": tgw.get("port"), "bind_host": tgw.get("bind_host")}


def check_action(client: TestClient, action: str) -> Dict[str, Any]:
    resp = client.post(f"/engines/tgw-webui/{action}")
    body = resp.json()
    assert resp.status_code == 200, f"action expected 200, got {resp.status_code}: {body}"
    assert body.get("ok") is True, f"action expected ok=true: {body}"
    assert body.get("action") == action, f"action mismatch: {body}"
    return {"http": resp.status_code, "ok": True, "action": action}


def main() -> int:
    env_state = {
        "LLM_CHAT_API_BASE": "http://127.0.0.1:8500",
        "LLM_INTENT_API_BASE": "http://127.0.0.1:8501",
        "LLM_SMALL_API_BASE": "http://127.0.0.1:8502",
        "TGW_WEBUI_ENABLED": "1",
        "TGW_WEBUI_PORT": "7860",
        "TGW_WEBUI_BIND_HOST": "127.0.0.1",
    }
    service_state = {"active": False}

    def fake_read_env() -> Dict[str, str]:
        return copy.deepcopy(env_state)

    def fake_write_env(new_env: Dict[str, str]) -> None:
        env_state.clear()
        env_state.update(copy.deepcopy(new_env))

    def fake_is_listening(port: int) -> bool:
        return (
            port == int(env_state.get("TGW_WEBUI_PORT", "7860"))
            and env_state.get("TGW_WEBUI_ENABLED") == "1"
            and service_state["active"]
        )

    def fake_systemctl_show(_unit: str) -> Dict[str, str]:
        return {"ActiveState": "active" if service_state["active"] else "inactive"}

    def fake_systemctl_start(_unit: str) -> None:
        service_state["active"] = True

    def fake_systemctl_stop(_unit: str) -> None:
        service_state["active"] = False

    def fake_systemctl_restart(_unit: str) -> None:
        service_state["active"] = True

    def fake_journal_tail(_unit: str, lines: int = 160):
        return [f"fake-log line {i+1}" for i in range(min(lines, 3))]

    client = TestClient(server.app)
    previous_tgw_unit = os.environ.get("SYSTEMD_TGW_WEBUI")
    os.environ["SYSTEMD_TGW_WEBUI"] = "llm-tgw-webui.service"

    summary: Dict[str, Any] = {}
    try:
        with ExitStack() as stack:
            stack.enter_context(patched(server, "read_env", fake_read_env))
            stack.enter_context(patched(server, "write_env", fake_write_env))
            stack.enter_context(patched(server, "_is_listening", fake_is_listening))
            stack.enter_context(patched(server, "_systemctl_show", fake_systemctl_show))
            stack.enter_context(patched(server, "_systemctl_start", fake_systemctl_start))
            stack.enter_context(patched(server, "_systemctl_stop", fake_systemctl_stop))
            stack.enter_context(patched(server, "_systemctl_restart", fake_systemctl_restart))
            stack.enter_context(patched(server, "_journal_tail", fake_journal_tail))

            summary["status"] = check_status_payload(client)
            summary["config"] = check_config(client, port=7860, bind_host="127.0.0.1")
            summary["start"] = check_action(client, action="start")
            summary["restart"] = check_action(client, action="restart")
            summary["stop"] = check_action(client, action="stop")
    finally:
        if previous_tgw_unit is None:
            os.environ.pop("SYSTEMD_TGW_WEBUI", None)
        else:
            os.environ["SYSTEMD_TGW_WEBUI"] = previous_tgw_unit

    print(json.dumps({"ok": True, "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
