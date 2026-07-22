from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

from llm_manager import server


def test_knob_updates_do_not_copy_global_secrets_into_project_env():
    server.GLOBAL_ENV_PATH.write_text("OPENAI_API_KEY=sk-test-placeholder\n")
    server.ENV_PATH.write_text("ENABLE_CHAT=1\n")
    result = server.set_knobs(server.Knobs(SMART_ASSISTANT_URL="http://127.0.0.1:9999/command"))

    local = server.ENV_PATH.read_text()
    assert "OPENAI_API_KEY" not in local
    assert "SMART_ASSISTANT_URL=http://127.0.0.1:9999/command" in local
    assert result["OPENAI_API_KEY"] == "***REDACTED***"
    assert os.stat(server.ENV_PATH).st_mode & 0o777 == 0o600


def test_env_writer_rejects_newline_injection():
    with pytest.raises(HTTPException) as exc:
        server.write_env({"SAFE": "ok\nINJECTED=value"})
    assert exc.value.status_code == 422
    assert "newlines" in str(exc.value.detail)
