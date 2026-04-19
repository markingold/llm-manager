#!/usr/bin/env python3
"""In-process smoke checks for TGW WebUI controls.

Covers:
1) engine status exposes tgw_webui payload
2) enable TGW WebUI with restart
3) disable TGW WebUI without restart
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


def check_status_payload(client: TestClient) -> Dict[str, Any]:
    resp = client.get("/engines/status")
    body = resp.json()
    assert resp.status_code == 200, f"status expected 200, got {resp.status_code}: {body}"
    assert "tgw_webui" in body.get("chat", {}), f"status missing tgw_webui: {body}"
    return {"http": resp.status_code, "ok": True}


def check_toggle(client: TestClient, enable: bool, restart: bool) -> Dict[str, Any]:
    resp = client.post(
        "/engines/chat/tgw-webui",
        json={"enabled": enable, "restart": restart},
    )
    body = resp.json()
    assert resp.status_code == 200, f"toggle expected 200, got {resp.status_code}: {body}"
    assert body.get("ok") is True, f"toggle expected ok=true: {body}"
    tgw = body.get("tgw_webui") or {}
    assert tgw.get("enabled") is enable, f"toggle enabled mismatch: {tgw}"
    assert body.get("restarted") is restart, f"toggle restart mismatch: {body}"
    return {"http": resp.status_code, "ok": True, "enabled": tgw.get("enabled"), "restarted": body.get("restarted")}


def main() -> int:
    env_state = {
        "LLM_CHAT_API_BASE": "http://127.0.0.1:8500",
        "LLM_INTENT_API_BASE": "http://127.0.0.1:8501",
        "LLM_SMALL_API_BASE": "http://127.0.0.1:8502",
        "TGW_CHAT_WEBUI_ENABLED": "0",
        "TGW_CHAT_WEBUI_PORT": "7860",
        "TGW_CHAT_WEBUI_BIND_HOST": "127.0.0.1",
    }

    def fake_read_env() -> Dict[str, str]:
        return copy.deepcopy(env_state)

    def fake_write_env(new_env: Dict[str, str]) -> None:
        env_state.clear()
        env_state.update(copy.deepcopy(new_env))

    def fake_is_listening(port: int) -> bool:
        return port == 7860 and env_state.get("TGW_CHAT_WEBUI_ENABLED") == "1"

    def fake_systemctl_restart(_unit: str) -> None:
        return None

    def fake_slot_backends() -> Dict[str, str]:
        return {"chat": "tgw", "intent": "tgw", "small": "tgw"}

    client = TestClient(server.app)
    previous_unit = os.environ.get("SYSTEMD_LLM_A")
    os.environ["SYSTEMD_LLM_A"] = "llm-a.service"

    summary: Dict[str, Any] = {}
    try:
        with ExitStack() as stack:
            stack.enter_context(patched(server, "read_env", fake_read_env))
            stack.enter_context(patched(server, "write_env", fake_write_env))
            stack.enter_context(patched(server, "_is_listening", fake_is_listening))
            stack.enter_context(patched(server, "_systemctl_restart", fake_systemctl_restart))
            stack.enter_context(patched(server, "read_slot_backends", fake_slot_backends))

            summary["status"] = check_status_payload(client)
            summary["enable"] = check_toggle(client, enable=True, restart=True)
            summary["disable"] = check_toggle(client, enable=False, restart=False)
    finally:
        if previous_unit is None:
            os.environ.pop("SYSTEMD_LLM_A", None)
        else:
            os.environ["SYSTEMD_LLM_A"] = previous_unit

    print(json.dumps({"ok": True, "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
