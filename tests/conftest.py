from __future__ import annotations

import os
from pathlib import Path

import pytest

from api import model_inspector, server


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_dir = tmp_path / "state"
    models_dir = tmp_path / "models"
    engines_dir = tmp_path / "engines"
    logs_dir = tmp_path / "logs"
    secrets_dir = tmp_path / "secrets"
    for path in (state_dir, models_dir, engines_dir, logs_dir, secrets_dir):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(server, "STATE_DIR", state_dir)
    monkeypatch.setattr(server, "PROVIDER_STATE_PATH", state_dir / "provider_runtime_state.json")
    monkeypatch.setattr(server, "SLOT_BACKENDS_PATH", state_dir / "slot_backends.json")
    monkeypatch.setattr(server, "PROVIDER_MODELS_PATH", tmp_path / "provider_models.json")
    monkeypatch.setattr(server, "PROVIDER_POLICIES_PATH", tmp_path / "provider_policies.json")
    monkeypatch.setattr(server, "MODELS_DIR", models_dir)
    monkeypatch.setattr(server, "ENGINES_ROOT", engines_dir)
    monkeypatch.setattr(server, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(server, "ENV_PATH", secrets_dir / ".env")
    monkeypatch.setattr(server, "GLOBAL_ENV_PATH", secrets_dir / "global.env")
    monkeypatch.setattr(model_inspector, "MODELS_DIR", str(models_dir))

    with server.JOB_LOCK:
        for fd in server.JOB_PIDFDS.values():
            try:
                os.close(fd)
            except OSError:
                pass
        server.JOBS.clear()
        server.JOB_PROCESSES.clear()
        server.JOB_PIDFDS.clear()
    yield

    with server.JOB_LOCK:
        for process in server.JOB_PROCESSES.values():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
        for fd in server.JOB_PIDFDS.values():
            try:
                os.close(fd)
            except OSError:
                pass
        server.JOBS.clear()
        server.JOB_PROCESSES.clear()
        server.JOB_PIDFDS.clear()
