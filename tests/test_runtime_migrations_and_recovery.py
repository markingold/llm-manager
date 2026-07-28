from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from llm_manager import server
from llm_manager.config_migrations import migrate_provider_models, migrate_provider_policies
from llm_manager.runtime_store import SQLiteRuntimeStore


def test_sqlite_store_imports_legacy_json_and_applies_numbered_migrations(tmp_path: Path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"spend_logs": [{"amount_usd": 1.25}], "custom": {"kept": True}}))
    store = SQLiteRuntimeStore(tmp_path / "runtime.db", legacy_state_path=legacy)

    assert store.schema_version() == 2
    state = store.read_state({"spend_logs": [], "default": True})
    assert state["spend_logs"] == [{"amount_usd": 1.25}]
    assert state["custom"] == {"kept": True}
    assert state["default"] is True
    assert (tmp_path / "runtime.db").stat().st_mode & 0o777 == 0o600
    assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_sqlite_store_rejects_newer_runtime_schema(tmp_path: Path):
    database = tmp_path / "runtime.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_migrations VALUES (999, 'future')")
    with pytest.raises(RuntimeError, match="newer than supported"):
        SQLiteRuntimeStore(database).schema_version()


def test_configuration_migrations_add_schema_capabilities_and_embed_policy():
    models, applied = migrate_provider_models(
        {
            "local": {"slots": [{"id": "chat", "capabilities": ["chat"]}]},
            "openrouter": {"free": [], "paid": []},
            "openai": {"allowed": [{"id": "chat-model", "supports_tools": True}]},
        }
    )
    assert applied
    assert models["schema_version"] == 3
    assert next(row for row in models["local"]["slots"] if row["id"] == "embed")["capabilities"] == ["embeddings"]
    assert models["openai"]["allowed"][0]["capabilities"] == ["chat", "completions", "tool_calling"]

    policies, applied = migrate_provider_policies({"task_overrides": {}})
    assert applied
    assert policies["schema_version"] == 3
    assert policies["task_overrides"]["embed"]["selection"]["local_first"][0] == "local"
    assert policies["evaluation"]["scheduler"]["biweekly_benchmark_interval_days"] == 14

    with pytest.raises(ValueError):
        migrate_provider_models({"schema_version": 999})


def test_evaluation_recovery_requeues_running_work():
    state = server.read_provider_runtime_state()
    state["evaluation_queue"] = [{"run_id": "run-1", "status": "running", "request": {"cases": [{}]}}]
    state["evaluation_runs"] = {"run-1": {"run_id": "run-1", "status": "running"}}
    server.write_provider_runtime_state(state)

    result = server._recover_evaluation_jobs()
    recovered = server.read_provider_runtime_state()
    assert result == {"requeued": 1, "cancelled": 0, "interrupted": 0}
    assert recovered["evaluation_queue"][0]["status"] == "queued"
    assert recovered["evaluation_runs"]["run-1"]["recovery_count"] == 1


def test_evaluation_recovery_finishes_cancellation_without_requeueing():
    state = server.read_provider_runtime_state()
    state["evaluation_queue"] = [{"run_id": "run-1", "status": "cancelling", "request": {}}]
    state["evaluation_runs"] = {"run-1": {"run_id": "run-1", "status": "cancelling"}}
    server.write_provider_runtime_state(state)

    result = server._recover_evaluation_jobs()
    recovered = server.read_provider_runtime_state()
    assert result == {"requeued": 0, "cancelled": 1, "interrupted": 0}
    assert recovered["evaluation_queue"][0]["status"] == "cancelled"
    assert recovered["evaluation_runs"]["run-1"]["status"] == "cancelled"


@pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="Linux pidfds are required for restart-safe attachment")
def test_managed_job_recovery_reattaches_and_can_cancel_safely():
    process = subprocess.Popen(["sleep", "30"], start_new_session=True)
    job = {
        "id": "recovered-job",
        "kind": "train",
        "args": {},
        "cmd": ["sleep", "30"],
        "pid": process.pid,
        "start_ts": time.time(),
        "end_ts": None,
        "status": "running",
        "log": str(server.LOGS_DIR / "recovered.log"),
    }
    identity = server._process_identity(process.pid)
    assert identity is not None
    server._runtime_store().upsert_job(job, identity)

    result = server._recover_managed_jobs()
    assert result["recovered_running"] == 1
    assert server.JOBS["recovered-job"]["status"] == "recovered_running"
    assert server.jobs_cancel("recovered-job") == {"ok": True, "status": "cancelling"}
    assert process.wait(timeout=3) == -15


def test_managed_job_recovery_marks_stale_identity_interrupted():
    job = {
        "id": "stale-job",
        "kind": "train",
        "args": {},
        "cmd": ["not-this-process"],
        "pid": os.getpid(),
        "start_ts": time.time(),
        "end_ts": None,
        "status": "running",
        "log": str(server.LOGS_DIR / "stale.log"),
    }
    server._runtime_store().upsert_job(
        job,
        {"process_start_ticks": -1, "boot_id": "wrong", "command_sha256": "wrong"},
    )
    result = server._recover_managed_jobs()
    assert result["interrupted"] == 1
    assert server.JOBS["stale-job"]["status"] == "interrupted"
