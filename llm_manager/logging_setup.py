from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_SENSITIVE = re.compile(
    r"(password|secret|token|api[_-]?key|authorization|cookie|prompt|"
    r"request_body|response_body)",
    re.IGNORECASE,
)


def _safe(value: Any, key: str = "") -> Any:
    if _SENSITIVE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _safe(v, str(k)) for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in list(value)[:100]]
    if isinstance(value, str):
        return value[:8192]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:8192]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        environment = os.getenv("APP_ENV", "production").lower()
        if environment == "prod":
            environment = "production"
        elif environment == "dev":
            environment = "development"
        payload = {
            "schema_version": 1,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "project_id": "llm-manager",
            "service_id": getattr(record, "service_id", "llm-manager-api"),
            "component": getattr(record, "component", "api"),
            "environment": environment,
            "event": getattr(
                record,
                "event",
                re.sub(r"[^a-z0-9]+", "_", message.lower()).strip("_")[:128] or "log",
            ),
            "msg": message[:1024],
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and key not in payload and key != "event":
                payload[key] = _safe(value, key)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
            payload["exception"] = self.formatException(record.exc_info)[-16384:]
        if record.levelno >= logging.ERROR:
            payload.setdefault("error_code", "unclassified_error")
            payload.setdefault("handled", False)
        return json.dumps(_safe(payload), separators=(",", ":"), ensure_ascii=False)


def configure_logging() -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.captureWarnings(True)
    logging.getLogger("uvicorn.access").setLevel(
        logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
