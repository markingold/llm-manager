from __future__ import annotations

import os
import subprocess

import pytest
from fastapi import HTTPException

from llm_manager import server


def _model(name: str):
    path = server.MODELS_DIR / name
    path.mkdir()
    (path / "config.json").write_text('{"model_type":"llama"}')
    return path


def _inspection() -> dict:
    return {
        "kind": "transformers",
        "recommended_backend": "tgw",
        "fallback_backends": [],
        "unsupported_reason": None,
        "capabilities": ["chat", "completions"],
    }


def test_model_switch_rolls_back_link_and_backend_on_restart_failure(monkeypatch: pytest.MonkeyPatch):
    old = _model("old")
    _model("new")
    link = server.MODELS_DIR / "chat_active_model"
    link.symlink_to(old, target_is_directory=True)
    server.write_slot_backends({"chat": "tgw", "intent": "tgw", "small": "tgw"})
    monkeypatch.setattr(server, "inspect_one", lambda _: _inspection())
    monkeypatch.setattr(server, "_bounce_engine", lambda _: (_ for _ in ()).throw(HTTPException(502, "restart failed")))

    with pytest.raises(HTTPException) as exc:
        server.switch_model("chat", "new", bounce=True, backend="tgw", require_loaded=True)
    assert exc.value.status_code == 502
    assert link.resolve() == old.resolve()
    assert server.read_slot_backends()["chat"] == "tgw"


def test_model_load_cannot_report_success_when_nothing_was_loaded(monkeypatch: pytest.MonkeyPatch):
    _model("new")
    monkeypatch.setattr(server, "inspect_one", lambda _: _inspection())
    with pytest.raises(HTTPException) as exc:
        server.switch_model("chat", "new", bounce=False, backend="tgw", require_loaded=True)
    assert exc.value.status_code == 409
    assert not (server.MODELS_DIR / "chat_active_model").exists()


def test_model_unload_rejects_noop_and_clears_link_after_engine_stop(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(HTTPException) as exc:
        server.model_unload(server.ModelUnloadReq(mode="chat", bounce=True))
    assert exc.value.status_code == 409

    active = _model("active")
    link = server.MODELS_DIR / "chat_active_model"
    link.symlink_to(active, target_is_directory=True)
    server.write_slot_backends({"chat": "tgw", "intent": "tgw", "small": "tgw"})
    monkeypatch.setattr(server, "_stop_engine", lambda _: {"ok": True, "method": "test"})
    result = server.model_unload(server.ModelUnloadReq(mode="chat", bounce=True))
    assert result["ok"] is True
    assert result["unload_method"] == "engine_stop"
    assert not link.exists() and not link.is_symlink()


def test_job_cancellation_uses_managed_process_identity_not_stored_pid():
    process = subprocess.Popen(["sleep", "30"], start_new_session=True)
    job_id = "managed"
    pidfd = os.pidfd_open(process.pid, 0) if hasattr(os, "pidfd_open") else None
    with server.JOB_LOCK:
        server.JOBS[job_id] = {
            "id": job_id,
            "pid": os.getpid(),  # Deliberately wrong: this value must never be signalled.
            "status": "running",
        }
        server.JOB_PROCESSES[job_id] = process
        if pidfd is not None:
            server.JOB_PIDFDS[job_id] = pidfd

    result = server.jobs_cancel(job_id)
    assert result == {"ok": True, "status": "cancelling"}
    assert process.wait(timeout=3) == -15


def test_job_cancellation_rejects_stale_records_without_signalling():
    server.JOBS["stale"] = {"id": "stale", "pid": os.getpid(), "status": "running"}
    with pytest.raises(HTTPException) as exc:
        server.jobs_cancel("stale")
    assert exc.value.status_code == 409
