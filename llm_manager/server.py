from __future__ import annotations

import fcntl
import functools
import hashlib
import importlib.util
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from .config_migrations import (
        CONFIG_SCHEMA_VERSION,
        MODEL_CAPABILITIES,
        migrate_provider_models,
        migrate_provider_policies,
    )
    from .evaluation.schemas import (
        EvalRerunRequest,
        EvalSuitePayload,
        EvalVariant,
        LocalEvalEnqueueResponse,
        LocalEvalRequest,
        LocalEvalResponse,
    )
    from .model_inspector import detect_kind, detect_loader, get_gpu_info, inspect_batch, inspect_one
    from .providers.local import LocalProviderAdapter
    from .providers.openai import OpenAIProviderAdapter
    from .providers.openrouter import OpenRouterProviderAdapter
    from .router.contracts import (
        RouterChatRequest,
        RouterChatResponse,
        RouterChoice,
        RouterCompletionChoice,
        RouterCompletionRequest,
        RouterCompletionResponse,
        RouterEmbedDatum,
        RouterEmbedRequest,
        RouterEmbedResponse,
        RouterModelPreferences,
        RouterProviderPreferences,
        RouterUsage,
    )
    from .runtime_store import SQLiteRuntimeStore
except ImportError:  # Support direct execution via `python llm_manager/server.py`.
    from config_migrations import (
        CONFIG_SCHEMA_VERSION,
        MODEL_CAPABILITIES,
        migrate_provider_models,
        migrate_provider_policies,
    )
    from evaluation.schemas import (
        EvalRerunRequest,
        EvalSuitePayload,
        EvalVariant,
        LocalEvalEnqueueResponse,
        LocalEvalRequest,
        LocalEvalResponse,
    )
    from model_inspector import detect_kind, detect_loader, get_gpu_info, inspect_batch, inspect_one
    from providers.local import LocalProviderAdapter
    from providers.openai import OpenAIProviderAdapter
    from providers.openrouter import OpenRouterProviderAdapter
    from router.contracts import (
        RouterChatRequest,
        RouterChatResponse,
        RouterChoice,
        RouterCompletionChoice,
        RouterCompletionRequest,
        RouterCompletionResponse,
        RouterEmbedDatum,
        RouterEmbedRequest,
        RouterEmbedResponse,
        RouterModelPreferences,
        RouterProviderPreferences,
        RouterUsage,
    )
    from runtime_store import SQLiteRuntimeStore

# -----------------------------------------------------------------------------
# Paths / config
# -----------------------------------------------------------------------------
PACKAGE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_ROOT.parent
ROOT = Path(
    os.getenv(
        "LLM_MANAGER_HOME",
        str(SOURCE_ROOT if (SOURCE_ROOT / "config").exists() else Path("/var/lib/llm-manager")),
    )
).resolve()
ENGINES_ROOT = Path(os.getenv("LLM_MANAGER_ENGINES_ROOT", "/srv/2bananas/engines")).resolve()
MODELS_DIR = Path(os.getenv("SERVER_MODELS_DIR", os.getenv("MODELS_DIR", "/srv/2bananas/engines/models")))
CONFIG_DIR = Path(os.getenv("LLM_MANAGER_CONFIG_DIR", str(ROOT / "config"))).resolve()
SECRETS_DIR = Path(os.getenv("LLM_MANAGER_SECRETS_DIR", str(ROOT / "secrets"))).resolve()
CONFIG_PATH = ROOT / "model_configs.json"
ENV_PATH = SECRETS_DIR / ".env"
GLOBAL_ENV_PATH = Path(os.getenv("LLM_MANAGER_GLOBAL_ENV_PATH", "/srv/2bananas/secrets/global.env"))
PROVIDER_MODELS_PATH = CONFIG_DIR / "provider_models.json"
PROVIDER_POLICIES_PATH = CONFIG_DIR / "provider_policies.json"
STATE_DIR = Path(os.getenv("LLM_MANAGER_STATE_DIR", str(ROOT / "run" / "state"))).resolve()
SLOT_BACKENDS_PATH = STATE_DIR / "slot_backends.json"
PROVIDER_STATE_PATH = STATE_DIR / "provider_runtime_state.json"
RUNTIME_DB_PATH = STATE_DIR / "runtime.db"
SCRIPTS_DIR = ROOT / "app" / "src" / "llm_manager"
LOGS_DIR = Path(os.getenv("LLM_MANAGER_LOG_DIR", str(ROOT / "run" / "logs"))).resolve()

SUPPORTED_BACKENDS = {"tgw", "vllm", "tabbyapi"}
SLOT_MODES = ("chat", "intent", "small", "embed")
DEFAULT_SLOT_BACKENDS = {"chat": "tgw", "intent": "tgw", "small": "tgw", "embed": "vllm"}
SLOT_DEFAULT_PORTS = {"chat": 8500, "intent": 8501, "small": 8502, "embed": 8503}
SWITCH_LIFECYCLE_MODES = {"legacy", "auto", "native"}
DEFAULT_TABBYAPI_NATIVE_MAX_SEQ_LEN = 16384
SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
POLICY_TASK_TYPES = {"chat", "completion", "embed"}
ROUTING_STRATEGIES = {"local_first", "free_first", "paid_first", "best_available", "strict_provider"}
ROUTING_LANES = {"local", "openrouter.free", "openrouter.paid", "openai"}
ROUTING_SERVICE_TIERS = {"default", "local", "low", "medium", "high"}
STRICT_PROVIDER_TASK_ALLOWED_LANES = {
    "chat": {"local", "openrouter.free", "openrouter.paid", "openai"},
    "completion": {"local", "openrouter.free", "openrouter.paid", "openai"},
    "embed": {"local", "openrouter.paid", "openai"},
}

DEFAULT_PROVIDER_MODELS = {
    "schema_version": CONFIG_SCHEMA_VERSION,
    "local": {"slots": []},
    "openrouter": {"free": [], "paid": []},
    "openai": {"allowed": []},
}

DEFAULT_PROVIDER_POLICIES = {
    "schema_version": CONFIG_SCHEMA_VERSION,
    "defaults": {
        "strategy": "local_first",
        "free_only": False,
        "paid_allowed": True,
        "allow_fallbacks": True,
        "preferred_provider": "local",
    },
    "openrouter": {
        "free_rate_limit_rpm": 20,
        "max_queue_depth": 100,
        "max_queue_wait_ms": 15000,
        "queue_behavior": "wait",
        "enforce_upstream_free_status": True,
        "quarantine_failure_count_24h": 6,
        "retire_failure_count_7d": 20,
        "cooldown_seconds_rate_limited": 30,
        "cooldown_seconds_quota_exhausted": 300,
        "cooldown_seconds_not_free_anymore": 900,
        "cooldown_seconds_model_unavailable": 120,
        "cooldown_seconds_provider_error": 20,
        "cooldown_seconds_retryable_default": 10,
        "auth_error_manual_review_threshold": 1,
    },
    "budget": {
        "daily_usd_limit": 10.0,
        "monthly_usd_limit": 200.0,
        "per_request_usd_limit": 1.0,
        "warn_threshold_pct": 0.8,
        "hard_fail_on_budget_exceeded": True,
        "providers": {
            "openrouter": True,
            "openai": True,
            "local": False,
        },
    },
    "selection": {
        "free_first": ["openrouter.free", "local", "openrouter.paid", "openai"],
        "local_first": ["local", "openrouter.free", "openrouter.paid", "openai"],
        "paid_first": ["openrouter.paid", "openai", "local", "openrouter.free"],
        "best_available": ["local", "openrouter.paid", "openai", "openrouter.free"],
        "strict_provider": [],
    },
    "dynamic_ranking": {
        "enabled": True,
        "strategies": ["local_first", "free_first", "paid_first", "best_available"],
        "weights": {
            "cost": 0.6,
            "availability": 0.3,
            "quality": 0.1,
        },
        "token_estimate": {
            "prompt_tokens": 500,
            "completion_tokens": 256,
        },
        "unknown_cost_score": 0.35,
    },
    "service_tiers": {
        "default": {
            "chain": [],
            "preferred_model_tags": [],
        },
        "local": {
            "chain": ["local"],
            "preferred_model_tags": ["local"],
        },
        "low": {
            "chain": ["local", "openrouter.free", "openrouter.paid", "openai"],
            "preferred_model_tags": ["cheap", "small"],
        },
        "medium": {
            "chain": ["local", "openrouter.free", "openrouter.paid", "openai"],
            "preferred_model_tags": ["balanced"],
        },
        "high": {
            "chain": ["openrouter.paid", "openai", "local", "openrouter.free"],
            "preferred_model_tags": ["quality", "reasoning"],
        },
    },
    "retention": {
        "request_logs_max": 200,
        "usage_logs_max": 1000,
        "spend_logs_max": 2000,
        "governance_audit_max": 500,
        "failure_events_max": 500,
        "failure_events_retention_days": 30,
        "promotion_transitions_max": 50,
        "smoke_checks_max": 30,
        "budget_daily_history_days": 60,
        "budget_monthly_history_months": 24,
    },
    "task_overrides": {},
    "project_overrides": {},
}

DEFAULT_PROVIDER_RUNTIME_STATE = {
    "provider_model_state": {},
    "provider_rate_limits": {
        "openrouter_free": {
            "rpm_limit": 20,
            "window_seconds": 60,
            "window_start": 0,
            "request_count": 0,
        }
    },
    "provider_request_queue": [],
    "request_logs": [],
    "usage_logs": [],
    "spend_logs": [],
    "budget_state": {
        "daily": {},
        "monthly": {},
        "lifetime_total_usd": 0.0,
        "last_spend_ts": None,
    },
    "openrouter_catalog_cache": {
        "fetched_ts": None,
        "count": 0,
        "free_ids": [],
        "models": [],
        "rankings": [],
        "rankings_fetched_ts": None,
        "rankings_error": None,
        "error": None,
    },
    "openrouter_free_candidates": {
        "updated_ts": None,
        "source_catalog_fetched_ts": None,
        "filters": {},
        "candidate_count": 0,
        "candidates": [],
        "active_ids": [],
        "activation_mode": "manual",
        "last_actor": None,
        "last_reason": None,
    },
    "evaluation_suites": {},
    "evaluation_runs": {},
    "evaluation_reports": {},
    "evaluation_queue": [],
    "conversion_runs": {},
    "conversion_artifacts": {},
    "updated_ts": None,
}

MAX_REQUEST_LOGS = 200
MAX_USAGE_LOGS = 1000
MAX_SPEND_LOGS = 2000
MAX_EVALUATION_RUNS = 200
MAX_EVALUATION_QUEUE = 500
MAX_CONVERSION_RUNS = 300

CONVERSION_TOKENIZER_FILES = [
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "tokenizer.model",
    "vocab.json",
    "merges.txt",
    "special_tokens_map.json",
]

EVAL_PRIORITY_ORDER = {"interactive": 0, "batch": 1, "evaluation": 2}
FREE_QUEUE_PRIORITY_ORDER = {"interactive": 0, "batch": 1, "evaluation": 2}
PROMOTION_STATES = {"discovered", "candidate", "smoke_passed", "active", "quarantined", "retired"}
FAILURE_WINDOW_24H_SECONDS = 24 * 60 * 60
FAILURE_WINDOW_7D_SECONDS = 7 * 24 * 60 * 60
EVAL_WORKER_TICK_SECONDS = 0.5
EVAL_WORKER_LOCK = threading.Lock()
EVAL_QUEUE_CLAIM_LOCK = threading.Lock()
CONVERSION_STATE_LOCK = threading.Lock()
PROVIDER_STATE_LOCK = threading.RLock()
PROVIDER_STATE_TRANSACTION = threading.local()
CONFIG_MIGRATION_LOCK = threading.RLock()
RUNTIME_STORE_LOCK = threading.Lock()
RUNTIME_STORE_CACHE: tuple[Path, Path, SQLiteRuntimeStore] | None = None
SLOT_STATE_LOCK = threading.RLock()
MODEL_LIFECYCLE_LOCK = threading.RLock()
EVAL_WORKER_STARTED = False
EVAL_WORKER_COUNT = max(1, int(os.getenv("EVAL_WORKER_COUNT", "2")))
EVAL_PRIORITY_RUNNING_CAPS = {
    "interactive": max(1, int(os.getenv("EVAL_MAX_RUNNING_INTERACTIVE", "2"))),
    "batch": max(1, int(os.getenv("EVAL_MAX_RUNNING_BATCH", "1"))),
    "evaluation": max(1, int(os.getenv("EVAL_MAX_RUNNING_EVALUATION", "1"))),
}
EVAL_RUNNING_STALE_SECONDS = max(60, int(os.getenv("EVAL_RUNNING_STALE_SECONDS", "900")))

# -----------------------------------------------------------------------------
# Defaults / env
# -----------------------------------------------------------------------------
DEFAULTS = {
  "LLM_CHAT_API_BASE":   os.getenv("LLM_CHAT_API_BASE",   "http://127.0.0.1:8500"),
  "LLM_INTENT_API_BASE": os.getenv("LLM_INTENT_API_BASE", "http://127.0.0.1:8501"),
    "LLM_SMALL_API_BASE":  os.getenv("LLM_SMALL_API_BASE",  "http://127.0.0.1:8502"),
    "LLM_EMBED_API_BASE":  os.getenv("LLM_EMBED_API_BASE",  "http://127.0.0.1:8503"),
    "LLM_CHAT_API_BASE_TGW": os.getenv("LLM_CHAT_API_BASE_TGW", ""),
    "LLM_CHAT_API_BASE_VLLM": os.getenv("LLM_CHAT_API_BASE_VLLM", ""),
    "LLM_CHAT_API_BASE_TABBYAPI": os.getenv("LLM_CHAT_API_BASE_TABBYAPI", ""),
    "LLM_INTENT_API_BASE_TGW": os.getenv("LLM_INTENT_API_BASE_TGW", ""),
    "LLM_INTENT_API_BASE_VLLM": os.getenv("LLM_INTENT_API_BASE_VLLM", ""),
    "LLM_INTENT_API_BASE_TABBYAPI": os.getenv("LLM_INTENT_API_BASE_TABBYAPI", ""),
    "LLM_SMALL_API_BASE_TGW": os.getenv("LLM_SMALL_API_BASE_TGW", ""),
    "LLM_SMALL_API_BASE_VLLM": os.getenv("LLM_SMALL_API_BASE_VLLM", ""),
    "LLM_SMALL_API_BASE_TABBYAPI": os.getenv("LLM_SMALL_API_BASE_TABBYAPI", ""),
    "LLM_EMBED_API_BASE_TGW": os.getenv("LLM_EMBED_API_BASE_TGW", ""),
    "LLM_EMBED_API_BASE_VLLM": os.getenv("LLM_EMBED_API_BASE_VLLM", ""),
    "LLM_EMBED_API_BASE_TABBYAPI": os.getenv("LLM_EMBED_API_BASE_TABBYAPI", ""),
  "SMART_ASSISTANT_URL": os.getenv("SMART_ASSISTANT_URL", "http://127.0.0.1:8100/command"),
  "CUDA_VISIBLE_DEVICES":os.getenv("CUDA_VISIBLE_DEVICES","0"),
  "PM2_CHAT":   os.getenv("PM2_CHAT",   "llm_chat"),
  "PM2_INTENT": os.getenv("PM2_INTENT", "llm_lora_intent"),
    "PM2_SMALL":  os.getenv("PM2_SMALL",  "llm_small"),
    "PM2_EMBED":  os.getenv("PM2_EMBED",  "llm_embed"),
    "TGW_CHAT_WEBUI_ENABLED": os.getenv("TGW_CHAT_WEBUI_ENABLED", "0"),
    "TGW_CHAT_WEBUI_PORT": os.getenv("TGW_CHAT_WEBUI_PORT", "7860"),
    "TGW_CHAT_WEBUI_BIND_HOST": os.getenv("TGW_CHAT_WEBUI_BIND_HOST", "127.0.0.1"),
    "TGW_CHAT_WEBUI_PUBLIC_URL": os.getenv("TGW_CHAT_WEBUI_PUBLIC_URL", ""),
    "TGW_WEBUI_ENABLED": os.getenv("TGW_WEBUI_ENABLED", os.getenv("TGW_CHAT_WEBUI_ENABLED", "0")),
    "TGW_WEBUI_PORT": os.getenv("TGW_WEBUI_PORT", os.getenv("TGW_CHAT_WEBUI_PORT", "7860")),
    "TGW_WEBUI_BIND_HOST": os.getenv("TGW_WEBUI_BIND_HOST", os.getenv("TGW_CHAT_WEBUI_BIND_HOST", "127.0.0.1")),
    "TGW_WEBUI_PUBLIC_URL": os.getenv("TGW_WEBUI_PUBLIC_URL", os.getenv("TGW_CHAT_WEBUI_PUBLIC_URL", "")),
}

# -----------------------------------------------------------------------------
# Bind knobs (systemd-friendly)
# -----------------------------------------------------------------------------
LLM_MANAGER_HOST = os.getenv("LLM_MANAGER_HOST", "127.0.0.1")
LLM_MANAGER_PORT = int(os.getenv("LLM_MANAGER_PORT", os.getenv("PORT", "8101")))

@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    _startup_runtime_reconciliation()
    yield


app = FastAPI(title="LLM Manager API", version="2.0", lifespan=_app_lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_log_middleware(request, call_next):
    start = time.time()
    request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
    response = None
    status = 500
    try:
        response = await call_next(request)
        status = int(response.status_code)
        return response
    finally:
        duration_ms = int((time.time() - start) * 1000)
        print(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": "info",
            "msg": "http_request",
            "project": "llm-manager",
            "component": "api",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "duration_ms": duration_ms,
        }, separators=(",", ":")))
        if response is not None:
            response.headers["X-Request-Id"] = request_id

# -----------------------------------------------------------------------------
# Helpers: env, models, symlinks
# -----------------------------------------------------------------------------
def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, v = stripped.split("=", 1)
        values[k.strip()] = v.strip()
    return values


def read_env() -> dict:
    env = DEFAULTS.copy()

    # Local project secrets are fallback values.
    for k, v in _read_env_file(ENV_PATH).items():
        env[k] = v

    # Shared global env values are preferred when present and non-empty.
    for k, v in _read_env_file(GLOBAL_ENV_PATH).items():
        if v:
            env[k] = v
    return env

def _atomic_write_text(path: Path, content: str, mode: int = 0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def write_env(env: dict):
    lines = []
    for key, value in sorted(env.items()):
        key_text = str(key).strip()
        value_text = str(value)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_text):
            raise HTTPException(422, f"invalid environment key: {key_text!r}")
        if "\n" in value_text or "\r" in value_text:
            raise HTTPException(422, f"environment value for {key_text} may not contain newlines")
        lines.append(f"{key_text}={value_text}")
    _atomic_write_text(ENV_PATH, "\n".join(lines) + "\n", mode=0o600)


def redacted_env(env: dict) -> dict:
    out = {}
    for k, v in env.items():
        if any(marker in k.upper() for marker in SENSITIVE_ENV_MARKERS):
            out[k] = "***REDACTED***" if v else ""
        else:
            out[k] = v
    return out


def redact_sensitive_data(value):
    """Remove credential-like values before user-controlled data is persisted."""
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(marker in str(key).upper() for marker in SENSITIVE_ENV_MARKERS)
                else redact_sensitive_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if (
            re.match(r"^(?:Bearer\s+)?(?:sk-[A-Za-z0-9_-]{12,}|hf_[A-Za-z0-9]{12,})$", stripped, re.IGNORECASE)
            or "-----BEGIN PRIVATE KEY-----" in stripped
        ):
            return "[REDACTED]"
    return value


def _env_flag(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _request_host_for_port(request: Request | None) -> str:
    if request is None:
        return ""
    raw = request.headers.get("X-Forwarded-Host") or request.headers.get("host") or ""
    raw = raw.split(",", 1)[0].strip()
    if not raw:
        return ""
    if raw.startswith("["):
        end = raw.find("]")
        return raw[: end + 1] if end != -1 else raw
    if raw.count(":") == 1:
        return raw.rsplit(":", 1)[0]
    return raw


def _tgw_webui_config(env: dict | None = None) -> dict:
    source = env or read_env()
    return {
        "enabled": _env_flag(source.get("TGW_WEBUI_ENABLED") or source.get("TGW_CHAT_WEBUI_ENABLED"), False),
        "port": _env_int(source.get("TGW_WEBUI_PORT") or source.get("TGW_CHAT_WEBUI_PORT"), 7860),
        "bind_host": str(source.get("TGW_WEBUI_BIND_HOST") or source.get("TGW_CHAT_WEBUI_BIND_HOST") or "127.0.0.1").strip() or "127.0.0.1",
        "public_url": str(source.get("TGW_WEBUI_PUBLIC_URL") or source.get("TGW_CHAT_WEBUI_PUBLIC_URL") or "").strip(),
    }


def _tgw_webui_launch_url(config: dict, request: Request | None = None) -> str:
    explicit = str(config.get("public_url") or "").strip()
    if explicit:
        return explicit
    host = _request_host_for_port(request)
    if host:
        return f"http://{host}:{config['port']}/"
    return f"http://127.0.0.1:{config['port']}/"


def _tgw_webui_state(
    env: dict | None = None,
    request: Request | None = None,
) -> dict:
    config = _tgw_webui_config(env)
    unit = os.getenv("SYSTEMD_TGW_WEBUI", "llm-tgw-webui.service")
    state = _systemctl_show(unit) if unit else {"error": "SYSTEMD unit not configured"}
    active_state = str(state.get("ActiveState") or "")
    active = active_state == "active"
    listening = _is_listening(config["port"]) if active else False
    return {
        "unit": unit,
        "service_configured": bool(unit),
        "enabled": config["enabled"],
        "active": active,
        "active_state": active_state,
        "systemd": state,
        "port": config["port"],
        "bind_host": config["bind_host"],
        "public_url": config["public_url"],
        "launch_url": _tgw_webui_launch_url(config, request=request),
        "listening": listening,
    }


def _tgw_webui_action(action: str):
    unit = os.getenv("SYSTEMD_TGW_WEBUI", "llm-tgw-webui.service")
    if not unit:
        raise HTTPException(400, "SYSTEMD unit not configured for TGW WebUI (SYSTEMD_TGW_WEBUI)")

    if action == "start":
        _systemctl_start(unit)
    elif action == "stop":
        _systemctl_stop(unit)
    elif action == "restart":
        _systemctl_restart(unit)
    else:
        raise HTTPException(400, "action must be start|stop|restart")
    return unit

def list_intent_models():
    if not MODELS_DIR.exists():
        return []
    return sorted([p.name for p in MODELS_DIR.iterdir() if p.is_dir() and p.name.startswith("lora_")])

def list_non_intent_models():
    if not MODELS_DIR.exists():
        return []
    ignore = {"intent_active_model", "chat_active_model", "small_active_model", "embed_active_model"}
    return sorted([
        p.name for p in MODELS_DIR.iterdir()
        if p.is_dir() and p.name not in ignore and not p.name.startswith("lora_")
    ])

def _make_symlink(link: Path, target: Path):
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, link)
    finally:
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink()


def _symlink_target(link: Path) -> Path | None:
    if not link.is_symlink():
        return None
    raw = Path(os.readlink(link))
    return raw.resolve() if raw.is_absolute() else (link.parent / raw).resolve()


def _restore_symlink(link: Path, previous_target: Path | None):
    if previous_target is None:
        if link.is_symlink() or link.exists():
            link.unlink()
        return
    _make_symlink(link, previous_target)


def _resolve_path_within(
    value: str | Path,
    roots: list[Path],
    *,
    must_exist: bool = False,
    must_be_dir: bool = False,
    label: str = "path",
) -> Path:
    raw = str(value or "").strip()
    if not raw or "\x00" in raw:
        raise HTTPException(422, f"{label} is required")

    requested = Path(raw)
    candidates = [requested] if requested.is_absolute() else [root / requested for root in roots]
    allowed_roots = [root.resolve() for root in roots]
    for candidate in candidates:
        resolved = candidate.resolve()
        if not any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
            continue
        if must_exist and not resolved.exists():
            continue
        if must_be_dir and (not resolved.exists() or not resolved.is_dir()):
            continue
        return resolved

    allowed = ", ".join(str(root) for root in allowed_roots)
    requirement = "existing directory within" if must_be_dir else "path within"
    raise HTTPException(422, f"{label} must be an {requirement}: {allowed}")


def _resolve_model_directory(model_dir: str) -> Path:
    return _resolve_path_within(
        model_dir,
        [MODELS_DIR],
        must_exist=True,
        must_be_dir=True,
        label="model_dir",
    )


def _validate_repo_id(repo_id: str) -> str:
    value = str(repo_id or "").strip()
    if (
        not value
        or value.startswith(('/', '\\'))
        or ".." in value.split("/")
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?", value)
    ):
        raise HTTPException(422, "repo_id must be a safe Hugging Face owner/model identifier")
    return value


def read_slot_backends() -> dict:
    with SLOT_STATE_LOCK:
        data = DEFAULT_SLOT_BACKENDS.copy()
        if SLOT_BACKENDS_PATH.exists():
            try:
                raw = json.loads(SLOT_BACKENDS_PATH.read_text())
                if isinstance(raw, dict):
                    for mode in SLOT_MODES:
                        backend = raw.get(mode)
                        if backend in SUPPORTED_BACKENDS:
                            data[mode] = backend
            except Exception:
                pass
        return data


def write_slot_backends(data: dict):
    with SLOT_STATE_LOCK:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(SLOT_BACKENDS_PATH, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o600)


def set_slot_backend(mode: str, backend: str):
    with SLOT_STATE_LOCK:
        current = read_slot_backends()
        current[mode] = backend
        write_slot_backends(current)


def _normalize_slot_mode(mode: str | None, default: str = "chat") -> str:
    raw = str(mode or "").strip().lower()
    if raw == "util":
        raw = "small"
    if raw in SLOT_MODES:
        return raw
    return default


def _slot_alias_for_mode(mode: str) -> str:
    mode_key = _normalize_slot_mode(mode)
    return {
        "chat": "chat_active_model",
        "intent": "intent_active_model",
        "small": "small_active_model",
        "embed": "embed_active_model",
    }.get(mode_key, "chat_active_model")


def _active_model_name_for_mode(mode: str) -> str | None:
    mode_key = _normalize_slot_mode(mode)
    active_path = str(current_links().get(mode_key, "") or "")
    if not active_path:
        return None
    return os.path.basename(active_path.rstrip("/")) if active_path else None


def _slot_mode_from_local_model(model_id: str, fallback_mode: str = "chat") -> str:
    fallback = _normalize_slot_mode(fallback_mode)
    model = str(model_id or "").strip()
    if not model:
        return fallback

    alias_map = {
        "chat_active_model": "chat",
        "intent_active_model": "intent",
        "small_active_model": "small",
        "embed_active_model": "embed",
    }
    if model in alias_map:
        return alias_map[model]

    local_ref = model
    if local_ref.startswith("local:"):
        local_ref = local_ref.split(":", 1)[1].strip()

    if local_ref in SLOT_MODES:
        return local_ref
    if ":" in local_ref:
        left = local_ref.split(":", 1)[0].strip().lower()
        if left in SLOT_MODES:
            return left

    active = current_links()
    for mode in SLOT_MODES:
        active_path = str(active.get(mode, "") or "")
        active_name = os.path.basename(active_path.rstrip("/")) if active_path else ""
        if model == active_name:
            return mode

    return fallback


def _local_base_env_key(mode: str) -> str:
    mode_key = _normalize_slot_mode(mode)
    return {
        "chat": "LLM_CHAT_API_BASE",
        "intent": "LLM_INTENT_API_BASE",
        "small": "LLM_SMALL_API_BASE",
        "embed": "LLM_EMBED_API_BASE",
    }.get(mode_key, "LLM_CHAT_API_BASE")


def _local_backend_base_env_key(mode: str, backend: str | None) -> str:
    backend_key = str(backend or "").strip().upper()
    if not backend_key:
        return ""
    return f"{_local_base_env_key(mode)}_{backend_key}"


def _local_slot_catalog_row(mode: str, provider_models: dict | None = None) -> dict:
    mode_key = _normalize_slot_mode(mode)
    doc = provider_models if isinstance(provider_models, dict) else read_provider_models()
    local = doc.get("local", {}) if isinstance(doc.get("local", {}), dict) else {}
    slots = local.get("slots", []) if isinstance(local.get("slots", []), list) else []
    for row in slots:
        if not isinstance(row, dict):
            continue
        slot_id = _normalize_slot_mode(row.get("id"), default="")
        if slot_id == mode_key:
            return row
    return {}


def _local_base_for_mode(
    mode: str,
    env: dict,
    backend: str | None = None,
    provider_models: dict | None = None,
) -> tuple[str, str]:
    mode_key = _normalize_slot_mode(mode)
    backend_key = _local_backend_base_env_key(mode_key, backend)
    if backend_key:
        backend_base = str(env.get(backend_key, "") or "").strip()
        if backend_base:
            return backend_base, backend_key

    slot_row = _local_slot_catalog_row(mode_key, provider_models=provider_models)
    backend_base_envs = slot_row.get("base_env_by_backend", {}) if isinstance(slot_row.get("base_env_by_backend", {}), dict) else {}
    backend_label = str(backend or "").strip().lower()
    mapped_env_key = str(backend_base_envs.get(backend_label, "") or "").strip()
    if mapped_env_key:
        mapped_base = str(env.get(mapped_env_key, "") or "").strip()
        if mapped_base:
            return mapped_base, mapped_env_key

    base_env_key = str(slot_row.get("base_env", "") or "").strip()
    if base_env_key:
        base_from_slot = str(env.get(base_env_key, "") or "").strip()
        if base_from_slot:
            return base_from_slot, base_env_key

    generic_env_key = _local_base_env_key(mode_key)
    generic_base = str(env.get(generic_env_key, "") or "").strip()
    if generic_base:
        return generic_base, generic_env_key

    default_port = int(SLOT_DEFAULT_PORTS.get(mode_key, 8500))
    return f"http://127.0.0.1:{default_port}", "default"


def _port_from_base(base: str, mode: str) -> int:
    fallback = int(SLOT_DEFAULT_PORTS.get(_normalize_slot_mode(mode), 0))
    try:
        parsed = urlparse(str(base or "").strip())
        if parsed.port is not None:
            return int(parsed.port)
    except Exception:
        pass
    return fallback


def _local_endpoint_for_model(
    model_id: str,
    env: dict,
    provider_models: dict | None = None,
    fallback_mode: str = "chat",
) -> dict:
    mode = _slot_mode_from_local_model(model_id, fallback_mode=fallback_mode)
    slot_backends = read_slot_backends()
    backend = str(slot_backends.get(mode, DEFAULT_SLOT_BACKENDS.get(mode, "tgw")) or "tgw").strip().lower()
    if backend not in SUPPORTED_BACKENDS:
        backend = DEFAULT_SLOT_BACKENDS.get(mode, "tgw")
    base, base_source = _local_base_for_mode(mode, env, backend=backend, provider_models=provider_models)
    active_model = _active_model_name_for_mode(mode)
    return {
        "mode": mode,
        "backend": backend,
        "base": str(base).rstrip("/"),
        "base_source": base_source,
        "active_model": active_model,
        "port": _port_from_base(base, mode),
    }


def _load_json(path: Path, default_obj: dict) -> dict:
    if not path.exists():
        return json.loads(json.dumps(default_obj))
    try:
        raw = json.loads(path.read_text())
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return json.loads(json.dumps(default_obj))


def _read_migrated_config(path: Path, default_obj: dict, migrator) -> dict:
    with CONFIG_MIGRATION_LOCK:
        document = _load_json(path, default_obj)
        try:
            migrated, applied = migrator(document)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if applied:
            if path.exists():
                original_version = document.get("schema_version", 1)
                backup = path.with_suffix(path.suffix + f".v{original_version}.bak")
                if not backup.exists():
                    _atomic_write_text(backup, path.read_text(), mode=0o600)
            _atomic_write_text(path, json.dumps(migrated, indent=2, sort_keys=True) + "\n", mode=0o600)
        return migrated


def read_provider_models() -> dict:
    return _read_migrated_config(PROVIDER_MODELS_PATH, DEFAULT_PROVIDER_MODELS, migrate_provider_models)


def read_provider_policies() -> dict:
    return _read_migrated_config(PROVIDER_POLICIES_PATH, DEFAULT_PROVIDER_POLICIES, migrate_provider_policies)


def _runtime_store() -> SQLiteRuntimeStore:
    global RUNTIME_STORE_CACHE
    db_path = Path(RUNTIME_DB_PATH)
    legacy_path = Path(PROVIDER_STATE_PATH)
    cached = RUNTIME_STORE_CACHE
    if cached is not None and cached[0] == db_path and cached[1] == legacy_path:
        return cached[2]
    with RUNTIME_STORE_LOCK:
        cached = RUNTIME_STORE_CACHE
        if cached is None or cached[0] != db_path or cached[1] != legacy_path:
            store = SQLiteRuntimeStore(db_path, legacy_state_path=legacy_path)
            RUNTIME_STORE_CACHE = (db_path, legacy_path, store)
        return RUNTIME_STORE_CACHE[2]


def read_provider_runtime_state() -> dict:
    with PROVIDER_STATE_LOCK:
        state = _runtime_store().read_state(DEFAULT_PROVIDER_RUNTIME_STATE)
        changed = _ensure_provider_runtime_state(state)
        if changed:
            if int(getattr(PROVIDER_STATE_TRANSACTION, "depth", 0) or 0) > 0:
                write_provider_runtime_state(state)
            else:
                with _provider_state_transaction():
                    state = _runtime_store().read_state(DEFAULT_PROVIDER_RUNTIME_STATE)
                    changed = _ensure_provider_runtime_state(state)
                    if changed:
                        write_provider_runtime_state(state)
        return state


def write_provider_runtime_state(state: dict):
    with PROVIDER_STATE_LOCK:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state = dict(state)
        state["updated_ts"] = datetime.utcnow().isoformat() + "Z"
        _runtime_store().write_state(state)


@contextmanager
def _provider_state_transaction():
    """Serialize state read/modify/write sequences across threads and workers."""
    with PROVIDER_STATE_LOCK:
        depth = int(getattr(PROVIDER_STATE_TRANSACTION, "depth", 0) or 0)
        lock_handle = None
        if depth == 0:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            lock_path = RUNTIME_DB_PATH.with_suffix(RUNTIME_DB_PATH.suffix + ".lock")
            lock_handle = lock_path.open("a+")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            PROVIDER_STATE_TRANSACTION.lock_handle = lock_handle
        PROVIDER_STATE_TRANSACTION.depth = depth + 1
        try:
            yield
        finally:
            remaining = int(getattr(PROVIDER_STATE_TRANSACTION, "depth", 1) or 1) - 1
            PROVIDER_STATE_TRANSACTION.depth = max(0, remaining)
            if remaining <= 0:
                held = getattr(PROVIDER_STATE_TRANSACTION, "lock_handle", None)
                if held is not None:
                    fcntl.flock(held.fileno(), fcntl.LOCK_UN)
                    held.close()
                PROVIDER_STATE_TRANSACTION.lock_handle = None


def _state_transactional(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        with _provider_state_transaction():
            return func(*args, **kwargs)
    return wrapped


def _model_lifecycle_transactional(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        with MODEL_LIFECYCLE_LOCK:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with (STATE_DIR / "model_lifecycle.lock").open("a+") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    return func(*args, **kwargs)
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    return wrapped


def _ensure_provider_runtime_state(state: dict) -> bool:
    changed = False
    defaults = {
        "provider_model_state": {},
        "provider_rate_limits": {
            "openrouter_free": {
                "rpm_limit": 20,
                "window_seconds": 60,
                "window_start": 0,
                "request_count": 0,
            }
        },
        "provider_request_queue": [],
        "request_logs": [],
        "usage_logs": [],
        "spend_logs": [],
        "budget_state": {
            "daily": {},
            "monthly": {},
            "lifetime_total_usd": 0.0,
            "last_spend_ts": None,
        },
        "openrouter_catalog_cache": {
            "fetched_ts": None,
            "count": 0,
            "free_ids": [],
            "models": [],
            "rankings": [],
            "rankings_fetched_ts": None,
            "rankings_error": None,
            "error": None,
        },
        "openrouter_free_candidates": {
            "updated_ts": None,
            "source_catalog_fetched_ts": None,
            "filters": {},
            "candidate_count": 0,
            "candidates": [],
            "active_ids": [],
            "activation_mode": "manual",
            "last_actor": None,
            "last_reason": None,
        },
        "evaluation_suites": {},
        "evaluation_runs": {},
        "evaluation_reports": {},
        "evaluation_queue": [],
        "conversion_runs": {},
        "conversion_artifacts": {},
        "governance_last_good": {},
        "governance_audit": [],
    }
    for k, dv in defaults.items():
        if k not in state:
            state[k] = json.loads(json.dumps(dv))
            changed = True
    return changed


MAX_GOVERNANCE_AUDIT = 500


def _write_json(path: Path, payload: dict):
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n", mode=0o600)


def _doc_version(payload: dict) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _governance_changed_top_keys(before_doc: dict, after_doc: dict) -> list[str]:
    before = before_doc if isinstance(before_doc, dict) else {}
    after = after_doc if isinstance(after_doc, dict) else {}
    keys = set(before.keys()) | set(after.keys())
    changed = [k for k in sorted(keys) if before.get(k) != after.get(k)]
    return changed


def _validate_provider_models_document(doc: dict):
    if not isinstance(doc, dict):
        raise HTTPException(422, {"errors": ["provider models document must be a JSON object"]})

    errors = []
    if doc.get("schema_version") != CONFIG_SCHEMA_VERSION:
        errors.append(f"schema_version must be {CONFIG_SCHEMA_VERSION}")
    for root in ("local", "openrouter", "openai"):
        if root not in doc:
            errors.append(f"missing root key '{root}'")
        elif not isinstance(doc.get(root), dict):
            errors.append(f"root key '{root}' must be an object")

    local = doc.get("local", {}) if isinstance(doc.get("local"), dict) else {}
    if "slots" not in local or not isinstance(local.get("slots"), list):
        errors.append("local.slots must be a list")

    openrouter = doc.get("openrouter", {}) if isinstance(doc.get("openrouter"), dict) else {}
    for k in ("free", "paid"):
        if k not in openrouter or not isinstance(openrouter.get(k), list):
            errors.append(f"openrouter.{k} must be a list")

    openai = doc.get("openai", {}) if isinstance(doc.get("openai"), dict) else {}
    if "allowed" not in openai or not isinstance(openai.get("allowed"), list):
        errors.append("openai.allowed must be a list")

    capability_buckets = [
        ("local.slots", local.get("slots", [])),
        ("openrouter.free", openrouter.get("free", [])),
        ("openrouter.paid", openrouter.get("paid", [])),
        ("openai.allowed", openai.get("allowed", [])),
    ]
    for bucket_name, rows in capability_buckets:
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f"{bucket_name}[{index}] must be an object")
                continue
            capabilities = row.get("capabilities")
            if not isinstance(capabilities, list) or not capabilities:
                errors.append(f"{bucket_name}[{index}].capabilities must be a non-empty list")
                continue
            normalized = [str(value).strip().lower() for value in capabilities]
            invalid = sorted(set(normalized) - MODEL_CAPABILITIES)
            if invalid:
                errors.append(f"{bucket_name}[{index}].capabilities contains invalid values: {', '.join(invalid)}")
            if len(normalized) != len(set(normalized)):
                errors.append(f"{bucket_name}[{index}].capabilities must not contain duplicates")

    if errors:
        raise HTTPException(422, {"errors": errors})


def _validate_provider_policies_document(doc: dict):
    if not isinstance(doc, dict):
        raise HTTPException(422, {"errors": ["provider policies document must be a JSON object"]})

    errors = []
    if doc.get("schema_version") != CONFIG_SCHEMA_VERSION:
        errors.append(f"schema_version must be {CONFIG_SCHEMA_VERSION}")
    for root in ("defaults", "openrouter", "budget", "selection"):
        if root not in doc:
            errors.append(f"missing root key '{root}'")
        elif not isinstance(doc.get(root), dict):
            errors.append(f"root key '{root}' must be an object")

    defaults = doc.get("defaults", {}) if isinstance(doc.get("defaults"), dict) else {}
    if "strategy" not in defaults:
        errors.append("defaults.strategy is required")

    defaults_strategy = str(defaults.get("strategy", "") or "").strip()
    if defaults_strategy and defaults_strategy not in ROUTING_STRATEGIES:
        errors.append(
            "defaults.strategy must be one of: " + ", ".join(sorted(ROUTING_STRATEGIES))
        )

    preferred_provider = str(defaults.get("preferred_provider", "") or "").strip().lower()
    if preferred_provider and preferred_provider not in {"local", "openrouter", "openrouter.free", "openrouter.paid", "openai"}:
        errors.append("defaults.preferred_provider must be one of: local, openrouter, openrouter.free, openrouter.paid, openai")

    service_tier = str(defaults.get("service_tier", "") or "").strip().lower()
    if service_tier and service_tier not in ROUTING_SERVICE_TIERS:
        errors.append("defaults.service_tier must be one of: default, local, low, medium, high")

    selection = doc.get("selection", {}) if isinstance(doc.get("selection"), dict) else {}
    if defaults.get("strategy") and defaults.get("strategy") not in selection:
        errors.append("defaults.strategy must exist in selection map")
    for strategy_name, lanes in selection.items():
        sname = str(strategy_name)
        if sname not in ROUTING_STRATEGIES:
            errors.append(f"selection.{sname} must use one of: {', '.join(sorted(ROUTING_STRATEGIES))}")
            continue
        if not isinstance(lanes, list):
            errors.append(f"selection.{sname} must be a list")
            continue
        bad_lanes = [str(lane) for lane in lanes if str(lane) not in ROUTING_LANES]
        if bad_lanes:
            errors.append(
                f"selection.{sname} contains invalid lanes: {', '.join(sorted(set(bad_lanes)))}"
            )

    dynamic_ranking = doc.get("dynamic_ranking", {})
    if dynamic_ranking is not None and not isinstance(dynamic_ranking, dict):
        errors.append("dynamic_ranking must be an object when provided")
    if isinstance(dynamic_ranking, dict):
        enabled = dynamic_ranking.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            errors.append("dynamic_ranking.enabled must be a boolean")

        strategies = dynamic_ranking.get("strategies")
        if strategies is not None:
            if not isinstance(strategies, list):
                errors.append("dynamic_ranking.strategies must be a list")
            else:
                invalid = [str(strategy) for strategy in strategies if str(strategy) not in ROUTING_STRATEGIES]
                if invalid:
                    errors.append(
                        "dynamic_ranking.strategies contains invalid values: "
                        + ", ".join(sorted(set(invalid)))
                    )

        weights = dynamic_ranking.get("weights")
        parsed_weights = []
        if weights is not None and not isinstance(weights, dict):
            errors.append("dynamic_ranking.weights must be an object")
        if isinstance(weights, dict):
            for field in ("cost", "availability", "quality"):
                if field not in weights:
                    continue
                raw = weights.get(field)
                if isinstance(raw, bool):
                    errors.append(f"dynamic_ranking.weights.{field} must be a non-negative number")
                    continue
                try:
                    value = float(raw)
                except Exception:
                    errors.append(f"dynamic_ranking.weights.{field} must be a non-negative number")
                    continue
                if value < 0:
                    errors.append(f"dynamic_ranking.weights.{field} must be >= 0")
                else:
                    parsed_weights.append(value)
            if parsed_weights and sum(parsed_weights) <= 0:
                errors.append("dynamic_ranking.weights must include at least one value > 0")

        token_estimate = dynamic_ranking.get("token_estimate")
        if token_estimate is not None and not isinstance(token_estimate, dict):
            errors.append("dynamic_ranking.token_estimate must be an object")
        if isinstance(token_estimate, dict):
            for field, minimum in (("prompt_tokens", 1), ("completion_tokens", 0)):
                if field not in token_estimate:
                    continue
                raw = token_estimate.get(field)
                if isinstance(raw, bool):
                    errors.append(f"dynamic_ranking.token_estimate.{field} must be an integer >= {minimum}")
                    continue
                try:
                    value = int(raw)
                except Exception:
                    errors.append(f"dynamic_ranking.token_estimate.{field} must be an integer >= {minimum}")
                    continue
                if value < minimum:
                    errors.append(f"dynamic_ranking.token_estimate.{field} must be >= {minimum}")

        if "unknown_cost_score" in dynamic_ranking:
            raw = dynamic_ranking.get("unknown_cost_score")
            if isinstance(raw, bool):
                errors.append("dynamic_ranking.unknown_cost_score must be a number between 0 and 1")
            else:
                try:
                    value = float(raw)
                except Exception:
                    errors.append("dynamic_ranking.unknown_cost_score must be a number between 0 and 1")
                else:
                    if value < 0 or value > 1:
                        errors.append("dynamic_ranking.unknown_cost_score must be between 0 and 1")

    openrouter_cfg = doc.get("openrouter", {}) if isinstance(doc.get("openrouter"), dict) else {}
    queue_behavior = str(openrouter_cfg.get("queue_behavior", "wait") or "wait").strip().lower()
    if queue_behavior not in {"wait", "fail_fast", "fallback_to_local", "upgrade_to_paid"}:
        errors.append("openrouter.queue_behavior must be one of: wait, fail_fast, fallback_to_local, upgrade_to_paid")

    openrouter_int_fields = {
        "free_rate_limit_rpm": 1,
        "max_queue_depth": 1,
        "max_queue_wait_ms": 1,
        "quarantine_failure_count_24h": 1,
        "retire_failure_count_7d": 1,
        "cooldown_seconds_rate_limited": 0,
        "cooldown_seconds_quota_exhausted": 0,
        "cooldown_seconds_not_free_anymore": 0,
        "cooldown_seconds_model_unavailable": 0,
        "cooldown_seconds_provider_error": 0,
        "cooldown_seconds_retryable_default": 0,
        "auth_error_manual_review_threshold": 1,
    }
    for field, min_value in openrouter_int_fields.items():
        if field not in openrouter_cfg:
            continue
        raw = openrouter_cfg.get(field)
        if isinstance(raw, bool):
            errors.append(f"openrouter.{field} must be an integer >= {min_value}")
            continue
        try:
            value = int(raw)
        except Exception:
            errors.append(f"openrouter.{field} must be an integer >= {min_value}")
            continue
        if value < min_value:
            errors.append(f"openrouter.{field} must be >= {min_value}")

    service_tiers = doc.get("service_tiers", {})
    if service_tiers is not None and not isinstance(service_tiers, dict):
        errors.append("service_tiers must be an object when provided")
    if isinstance(service_tiers, dict):
        for tier_name, tier_row in service_tiers.items():
            tname = str(tier_name).strip().lower()
            if tname not in ROUTING_SERVICE_TIERS:
                errors.append(f"service_tiers.{tier_name} must use one of: default, local, low, medium, high")
                continue
            if not isinstance(tier_row, dict):
                errors.append(f"service_tiers.{tier_name} must be an object")
                continue
            chain = tier_row.get("chain", [])
            tags = tier_row.get("preferred_model_tags", [])
            if chain is not None and not isinstance(chain, list):
                errors.append(f"service_tiers.{tier_name}.chain must be a list")
            if tags is not None and not isinstance(tags, list):
                errors.append(f"service_tiers.{tier_name}.preferred_model_tags must be a list")
            if isinstance(chain, list):
                invalid = [str(lane) for lane in chain if str(lane) not in ROUTING_LANES]
                if invalid:
                    errors.append(
                        f"service_tiers.{tier_name}.chain contains invalid lanes: {', '.join(sorted(set(invalid)))}"
                    )

    task_overrides = doc.get("task_overrides", {})
    if task_overrides is not None and not isinstance(task_overrides, dict):
        errors.append("task_overrides must be an object when provided")

    root_task_overrides = task_overrides if isinstance(task_overrides, dict) else {}
    for task_type, override in root_task_overrides.items():
        task_key = str(task_type)
        if task_key not in POLICY_TASK_TYPES:
            errors.append(f"task_overrides.{task_key} must use one of: chat, completion, embed")
            continue
        if not isinstance(override, dict):
            errors.append(f"task_overrides.{task_key} must be an object")
            continue
        td = override.get("defaults", {})
        tsel = override.get("selection", {})
        if td is not None and not isinstance(td, dict):
            errors.append(f"task_overrides.{task_key}.defaults must be an object")
        if tsel is not None and not isinstance(tsel, dict):
            errors.append(f"task_overrides.{task_key}.selection must be an object")
        if isinstance(td, dict):
            strat = str(td.get("strategy", "") or "").strip()
            if strat and strat not in (tsel if isinstance(tsel, dict) else {}) and strat not in selection:
                errors.append(
                    f"task_overrides.{task_key}.defaults.strategy must exist in task or global selection map"
                )

    project_overrides = doc.get("project_overrides", {})
    if project_overrides is not None and not isinstance(project_overrides, dict):
        errors.append("project_overrides must be an object when provided")
    if isinstance(project_overrides, dict):
        for project_id, override in project_overrides.items():
            if not isinstance(override, dict):
                errors.append(f"project_overrides.{project_id} must be an object")
                continue
            od = override.get("defaults", {})
            osel = override.get("selection", {})
            otask = override.get("task_overrides", {})
            if od is not None and not isinstance(od, dict):
                errors.append(f"project_overrides.{project_id}.defaults must be an object")
            if osel is not None and not isinstance(osel, dict):
                errors.append(f"project_overrides.{project_id}.selection must be an object")
            if otask is not None and not isinstance(otask, dict):
                errors.append(f"project_overrides.{project_id}.task_overrides must be an object")
            if isinstance(od, dict) and isinstance(osel, dict):
                strat = str(od.get("strategy", "") or "").strip()
                if strat and strat not in osel and strat not in selection:
                    errors.append(
                        f"project_overrides.{project_id}.defaults.strategy must exist in project or global selection map"
                    )
            if isinstance(otask, dict):
                for task_type, task_override in otask.items():
                    task_key = str(task_type)
                    if task_key not in POLICY_TASK_TYPES:
                        errors.append(
                            f"project_overrides.{project_id}.task_overrides.{task_key} must use one of: chat, completion, embed"
                        )
                        continue
                    if not isinstance(task_override, dict):
                        errors.append(f"project_overrides.{project_id}.task_overrides.{task_key} must be an object")
                        continue
                    td = task_override.get("defaults", {})
                    tsel = task_override.get("selection", {})
                    if td is not None and not isinstance(td, dict):
                        errors.append(f"project_overrides.{project_id}.task_overrides.{task_key}.defaults must be an object")
                    if tsel is not None and not isinstance(tsel, dict):
                        errors.append(f"project_overrides.{project_id}.task_overrides.{task_key}.selection must be an object")
                    if isinstance(td, dict):
                        strat = str(td.get("strategy", "") or "").strip()
                        if strat:
                            effective_selection = dict(selection)
                            root_task = root_task_overrides.get(task_key, {}) if isinstance(root_task_overrides.get(task_key), dict) else {}
                            root_task_selection = root_task.get("selection", {}) if isinstance(root_task.get("selection"), dict) else {}
                            effective_selection.update(root_task_selection)
                            if isinstance(osel, dict):
                                effective_selection.update(osel)
                            if isinstance(tsel, dict):
                                effective_selection.update(tsel)
                            if strat not in effective_selection:
                                errors.append(
                                    f"project_overrides.{project_id}.task_overrides.{task_key}.defaults.strategy must exist in task/project/global selection map"
                                )

    budget = doc.get("budget", {}) if isinstance(doc.get("budget"), dict) else {}
    providers = budget.get("providers", {}) if isinstance(budget.get("providers"), dict) else None
    if providers is None:
        errors.append("budget.providers must be an object")

    retention = doc.get("retention", {})
    if retention is not None and not isinstance(retention, dict):
        errors.append("retention must be an object when provided")
    if isinstance(retention, dict):
        retention_int_fields = {
            "request_logs_max": 1,
            "usage_logs_max": 1,
            "spend_logs_max": 1,
            "governance_audit_max": 1,
            "failure_events_max": 1,
            "failure_events_retention_days": 8,
            "promotion_transitions_max": 1,
            "smoke_checks_max": 1,
            "budget_daily_history_days": 1,
            "budget_monthly_history_months": 1,
        }
        for field, min_value in retention_int_fields.items():
            if field not in retention:
                continue
            raw = retention.get(field)
            if isinstance(raw, bool):
                errors.append(f"retention.{field} must be an integer >= {min_value}")
                continue
            try:
                value = int(raw)
            except Exception:
                errors.append(f"retention.{field} must be an integer >= {min_value}")
                continue
            if value < min_value:
                errors.append(f"retention.{field} must be >= {min_value}")

    if errors:
        raise HTTPException(422, {"errors": errors})


def _retention_int(raw_value, default: int, minimum: int = 1, maximum: int = 100000) -> int:
    if isinstance(raw_value, bool):
        return default
    try:
        value = int(raw_value)
    except Exception:
        return default
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _retention_policy_settings(policies: dict | None = None) -> dict:
    policy_doc = policies if isinstance(policies, dict) else read_provider_policies()
    retention = policy_doc.get("retention", {}) if isinstance(policy_doc.get("retention"), dict) else {}
    return {
        "request_logs_max": _retention_int(retention.get("request_logs_max", MAX_REQUEST_LOGS), MAX_REQUEST_LOGS, 1, 100000),
        "usage_logs_max": _retention_int(retention.get("usage_logs_max", MAX_USAGE_LOGS), MAX_USAGE_LOGS, 1, 200000),
        "spend_logs_max": _retention_int(retention.get("spend_logs_max", MAX_SPEND_LOGS), MAX_SPEND_LOGS, 1, 500000),
        "governance_audit_max": _retention_int(retention.get("governance_audit_max", MAX_GOVERNANCE_AUDIT), MAX_GOVERNANCE_AUDIT, 1, 500000),
        "failure_events_max": _retention_int(retention.get("failure_events_max", 500), 500, 1, 100000),
        "failure_events_retention_days": _retention_int(retention.get("failure_events_retention_days", 30), 30, 8, 3650),
        "promotion_transitions_max": _retention_int(retention.get("promotion_transitions_max", 50), 50, 1, 100000),
        "smoke_checks_max": _retention_int(retention.get("smoke_checks_max", 30), 30, 1, 100000),
        "budget_daily_history_days": _retention_int(retention.get("budget_daily_history_days", 60), 60, 1, 3650),
        "budget_monthly_history_months": _retention_int(retention.get("budget_monthly_history_months", 24), 24, 1, 600),
    }


@_state_transactional
def _append_governance_audit(entry: dict):
    state = read_provider_runtime_state()
    retention = _retention_policy_settings()
    rows = state.get("governance_audit", []) if isinstance(state.get("governance_audit"), list) else []
    rows.append(entry)
    state["governance_audit"] = rows[-retention["governance_audit_max"]:]
    write_provider_runtime_state(state)
    return {
        "ts": entry.get("ts"),
        "resource": entry.get("resource"),
        "action": entry.get("action"),
    }


@_state_transactional
def _governance_apply(
    resource: str,
    target_path: Path,
    current_doc: dict,
    new_doc: dict,
    expected_version: str | None,
    actor: str,
    reason: str,
    validate_only: bool,
):
    current_doc = _load_json(target_path, current_doc)
    current_version = _doc_version(current_doc)
    if expected_version and str(expected_version) != current_version:
        raise HTTPException(409, {
            "message": "version conflict",
            "resource": resource,
            "expected_version": str(expected_version),
            "current_version": current_version,
        })

    if resource == "models":
        _validate_provider_models_document(new_doc)
    elif resource == "policies":
        _validate_provider_policies_document(new_doc)
    else:
        raise HTTPException(400, "unsupported governance resource")

    after_version = _doc_version(new_doc)
    changed_keys = _governance_changed_top_keys(current_doc, new_doc)

    if validate_only:
        audit_ref = _append_governance_audit({
            "ts": datetime.utcnow().isoformat() + "Z",
            "resource": resource,
            "action": "validate",
            "actor": actor,
            "reason": reason or "validate_only",
            "expected_version": expected_version,
            "before_version": current_version,
            "after_version": after_version,
            "changed_keys": changed_keys,
            "outcome": "validated",
        })
        return {
            "ok": True,
            "resource": resource,
            "outcome": "validated",
            "version": current_version,
            "next_version": after_version,
            "changed_keys": changed_keys,
            "audit_ref": audit_ref,
        }

    if not reason.strip():
        raise HTTPException(422, {"errors": ["reason is required for apply operations"]})

    state = read_provider_runtime_state()
    snaps = state.get("governance_last_good", {}) if isinstance(state.get("governance_last_good"), dict) else {}
    snaps[resource] = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "version": current_version,
        "document": current_doc,
    }
    state["governance_last_good"] = snaps
    write_provider_runtime_state(state)

    _write_json(target_path, new_doc)

    audit_ref = _append_governance_audit({
        "ts": datetime.utcnow().isoformat() + "Z",
        "resource": resource,
        "action": "apply",
        "actor": actor,
        "reason": reason,
        "expected_version": expected_version,
        "before_version": current_version,
        "after_version": after_version,
        "changed_keys": changed_keys,
        "outcome": "applied",
    })

    return {
        "ok": True,
        "resource": resource,
        "outcome": "applied",
        "version": after_version,
        "previous_version": current_version,
        "changed_keys": changed_keys,
        "audit_ref": audit_ref,
    }


@_state_transactional
def _governance_rollback(resource: str, target_path: Path, current_doc: dict, expected_version: str | None, actor: str, reason: str):
    current_doc = _load_json(target_path, current_doc)
    current_version = _doc_version(current_doc)
    if expected_version and str(expected_version) != current_version:
        raise HTTPException(409, {
            "message": "version conflict",
            "resource": resource,
            "expected_version": str(expected_version),
            "current_version": current_version,
        })

    if not reason.strip():
        raise HTTPException(422, {"errors": ["reason is required for rollback operations"]})

    state = read_provider_runtime_state()
    snaps = state.get("governance_last_good", {}) if isinstance(state.get("governance_last_good"), dict) else {}
    snapshot = snaps.get(resource)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("document"), dict):
        raise HTTPException(404, {"message": "no rollback snapshot available", "resource": resource})

    rollback_doc = snapshot["document"]
    rollback_version = _doc_version(rollback_doc)

    if resource == "models":
        _validate_provider_models_document(rollback_doc)
    elif resource == "policies":
        _validate_provider_policies_document(rollback_doc)

    _write_json(target_path, rollback_doc)

    audit_ref = _append_governance_audit({
        "ts": datetime.utcnow().isoformat() + "Z",
        "resource": resource,
        "action": "rollback",
        "actor": actor,
        "reason": reason,
        "expected_version": expected_version,
        "before_version": current_version,
        "after_version": rollback_version,
        "changed_keys": _governance_changed_top_keys(current_doc, rollback_doc),
        "outcome": "rolled_back",
    })

    return {
        "ok": True,
        "resource": resource,
        "outcome": "rolled_back",
        "version": rollback_version,
        "previous_version": current_version,
        "snapshot_ts": snapshot.get("ts"),
        "audit_ref": audit_ref,
    }


@_state_transactional
def _append_router_decision(decision: dict, policies: dict | None = None):
    decision = redact_sensitive_data(decision)
    state = read_provider_runtime_state()
    retention = _retention_policy_settings(policies)
    logs = state.get("request_logs", [])
    if not isinstance(logs, list):
        logs = []
    logs.append(decision)
    state["request_logs"] = logs[-retention["request_logs_max"]:]

    usage = state.get("usage_logs", [])
    if not isinstance(usage, list):
        usage = []
    usage.append({
        "ts": decision.get("ts"),
        "provider": decision.get("selected_provider"),
        "lane": decision.get("selected_lane"),
        "model": decision.get("selected_model"),
        "strategy": decision.get("strategy"),
        "prompt_tokens": decision.get("usage", {}).get("prompt_tokens", 0),
        "completion_tokens": decision.get("usage", {}).get("completion_tokens", 0),
        "total_tokens": decision.get("usage", {}).get("total_tokens", 0),
        "estimated_cost_usd": decision.get("estimated_cost_usd"),
    })
    state["usage_logs"] = usage[-retention["usage_logs_max"]:]
    write_provider_runtime_state(state)


def _prune_promotion_transitions(rows: list[dict], max_rows: int | None = None) -> list[dict]:
    transitions = rows if isinstance(rows, list) else []
    retention = _retention_policy_settings()
    keep_default = retention["promotion_transitions_max"]
    keep_rows = keep_default
    try:
        if max_rows is not None:
            keep_rows = max(1, int(max_rows))
    except Exception:
        keep_rows = keep_default
    out = [row for row in transitions if isinstance(row, dict)]
    return out[-keep_rows:]


def _prune_smoke_checks(rows: list[dict], max_rows: int | None = None) -> list[dict]:
    checks = rows if isinstance(rows, list) else []
    retention = _retention_policy_settings()
    keep_default = retention["smoke_checks_max"]
    keep_rows = keep_default
    try:
        if max_rows is not None:
            keep_rows = max(1, int(max_rows))
    except Exception:
        keep_rows = keep_default
    out = [row for row in checks if isinstance(row, dict)]
    return out[-keep_rows:]


def _set_promotion_state_on_row(row: dict, new_state: str, reason: str = "", actor: str = "system", at_ts: str | None = None):
    if not isinstance(row, dict):
        return
    state = str(new_state or "").strip().lower()
    if state not in PROMOTION_STATES:
        return
    ts = str(at_ts or (datetime.utcnow().isoformat() + "Z"))
    prev = str(row.get("promotion_state", "") or "")
    if prev != state:
        transitions = row.get("promotion_transitions", [])
        if not isinstance(transitions, list):
            transitions = []
        transitions.append({
            "ts": ts,
            "from": prev or None,
            "to": state,
            "reason": reason or None,
            "actor": actor,
        })
        row["promotion_transitions"] = _prune_promotion_transitions(transitions)
    row["promotion_state"] = state
    row["promotion_state_updated_ts"] = ts
    if reason:
        row["promotion_state_reason"] = reason


def _prune_failure_events(
    events: list[dict],
    now_ts: float | None = None,
    retention_days: int | None = None,
    max_events: int | None = None,
) -> list[dict]:
    rows = events if isinstance(events, list) else []
    now_epoch = float(now_ts or time.time())
    retention = _retention_policy_settings()
    keep_days_default = retention["failure_events_retention_days"]
    keep_max_default = retention["failure_events_max"]
    keep_days = keep_days_default
    keep_max = keep_max_default
    try:
        if retention_days is not None:
            keep_days = max(1, int(retention_days))
    except Exception:
        keep_days = keep_days_default
    try:
        if max_events is not None:
            keep_max = max(1, int(max_events))
    except Exception:
        keep_max = keep_max_default
    keep_cutoff = now_epoch - (keep_days * 24 * 60 * 60)
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = _parse_iso_ts(str(row.get("ts", "") or ""))
        if ts is None:
            continue
        if ts.timestamp() >= keep_cutoff:
            out.append(row)
    return out[-keep_max:]


def _failure_window_counts(row: dict, now_ts: float | None = None) -> dict[str, int]:
    now_epoch = float(now_ts or time.time())
    events = _prune_failure_events(row.get("failure_events", []) if isinstance(row, dict) else [], now_ts=now_epoch)
    cutoff_24h = now_epoch - FAILURE_WINDOW_24H_SECONDS
    cutoff_7d = now_epoch - FAILURE_WINDOW_7D_SECONDS
    c24 = 0
    c7d = 0
    for event in events:
        ts = _parse_iso_ts(str(event.get("ts", "") or ""))
        if ts is None:
            continue
        epoch = ts.timestamp()
        if epoch >= cutoff_7d:
            c7d += 1
            if epoch >= cutoff_24h:
                c24 += 1
    return {
        "failure_count_24h": c24,
        "failure_count_7d": c7d,
        "events": events,
    }


def _refresh_failure_window_fields(row: dict, now_ts: float | None = None) -> dict[str, int]:
    counts = _failure_window_counts(row, now_ts=now_ts)
    row["failure_events"] = counts.get("events", [])
    row["failure_count_24h"] = int(counts.get("failure_count_24h", 0) or 0)
    row["failure_count_7d"] = int(counts.get("failure_count_7d", 0) or 0)
    return counts


def _openrouter_expiration_is_past(expiration_value: str | None) -> bool:
    raw = str(expiration_value or "").strip()
    if not raw:
        return False
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        raw = f"{raw}T23:59:59Z"
    dt = _parse_iso_ts(raw)
    if dt is None:
        return False
    return dt.timestamp() <= time.time()


def _provider_model_state_row(provider: str, model: str) -> dict:
    state = read_provider_runtime_state()
    model_state = state.get("provider_model_state", {})
    if not isinstance(model_state, dict):
        return {}
    row = model_state.get(_provider_model_key(provider, model), {})
    if not isinstance(row, dict):
        return {}
    _refresh_failure_window_fields(row)
    row["promotion_transitions"] = _prune_promotion_transitions(row.get("promotion_transitions", []))
    row["smoke_checks"] = _prune_smoke_checks(row.get("smoke_checks", []))
    if not isinstance(row.get("last_smoke_check"), dict):
        row["last_smoke_check"] = row.get("smoke_checks", [])[-1] if row.get("smoke_checks") else None
    return row


def _provider_model_key(provider: str, model: str) -> str:
    return f"{provider}:{model}"


@_state_transactional
def _set_provider_model_promotion_state(provider: str, model: str, promotion_state: str, reason: str = "", actor: str = "system"):
    state = read_provider_runtime_state()
    model_state = state.get("provider_model_state", {})
    if not isinstance(model_state, dict):
        model_state = {}
    key = _provider_model_key(provider, model)
    row = model_state.get(key, {}) if isinstance(model_state.get(key), dict) else {}
    _set_promotion_state_on_row(row, promotion_state, reason=reason, actor=actor)
    if promotion_state in {"quarantined", "retired"}:
        row["exclude_from_free_rotation"] = True
    model_state[key] = row
    state["provider_model_state"] = model_state
    write_provider_runtime_state(state)


@_state_transactional
def _append_openrouter_smoke_evidence(model_id: str, evidence: dict):
    if not str(model_id or "").strip() or not isinstance(evidence, dict):
        return
    state = read_provider_runtime_state()
    model_state = state.get("provider_model_state", {}) if isinstance(state.get("provider_model_state"), dict) else {}
    key = _provider_model_key("openrouter", str(model_id))
    row = model_state.get(key, {}) if isinstance(model_state.get(key), dict) else {}
    checks = row.get("smoke_checks", []) if isinstance(row.get("smoke_checks"), list) else []
    checks.append(dict(evidence))
    checks = _prune_smoke_checks(checks)
    row["smoke_checks"] = checks
    row["last_smoke_check"] = checks[-1] if checks else dict(evidence)
    status = str(evidence.get("status", "") or "").lower()
    ts = str(evidence.get("ts", "") or "")
    if status == "passed":
        row["last_smoke_pass_ts"] = ts or row.get("last_smoke_pass_ts")
    elif status == "failed":
        row["last_smoke_fail_ts"] = ts or row.get("last_smoke_fail_ts")
    model_state[key] = row
    state["provider_model_state"] = model_state
    write_provider_runtime_state(state)


@_state_transactional
def _sync_openrouter_candidate_states(payload: dict, actor: str = "system", reason: str = ""):
    if not isinstance(payload, dict):
        return
    candidates = payload.get("candidates", []) if isinstance(payload.get("candidates", []), list) else []
    active_ids = {
        str(mid)
        for mid in (payload.get("active_ids", []) if isinstance(payload.get("active_ids", []), list) else [])
        if str(mid)
    }
    state = read_provider_runtime_state()
    model_state = state.get("provider_model_state", {})
    if not isinstance(model_state, dict):
        model_state = {}

    now_iso = datetime.utcnow().isoformat() + "Z"
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        model_id = str(candidate.get("id", "") or "").strip()
        if not model_id:
            continue
        key = _provider_model_key("openrouter", model_id)
        row = model_state.get(key, {}) if isinstance(model_state.get(key), dict) else {}

        if _openrouter_expiration_is_past(candidate.get("expiration_date")):
            row["exclude_from_free_rotation"] = True
            _set_promotion_state_on_row(row, "retired", reason="expired", actor=actor, at_ts=now_iso)
        elif model_id in active_ids:
            _set_promotion_state_on_row(row, "active", reason=reason or "candidate_activation", actor=actor, at_ts=now_iso)
        else:
            current = str(row.get("promotion_state", "") or "").lower()
            if current in {"", "discovered"}:
                _set_promotion_state_on_row(row, "candidate", reason=reason or "candidate_selected", actor=actor, at_ts=now_iso)
            elif current == "active":
                _set_promotion_state_on_row(row, "smoke_passed", reason="active_demoted", actor=actor, at_ts=now_iso)

        if bool(row.get("disabled_until_manual_review", False)) or bool(row.get("exclude_from_free_rotation", False)):
            if str(row.get("promotion_state", "") or "").lower() != "retired":
                _set_promotion_state_on_row(row, "quarantined", reason="flagged", actor=actor, at_ts=now_iso)

        _refresh_failure_window_fields(row)
        model_state[key] = row

    state["provider_model_state"] = model_state
    write_provider_runtime_state(state)


def _hydrate_openrouter_candidate_runtime_fields(payload: dict):
    if not isinstance(payload, dict):
        return
    candidates = payload.get("candidates", []) if isinstance(payload.get("candidates", []), list) else []
    active_ids = {
        str(mid)
        for mid in (payload.get("active_ids", []) if isinstance(payload.get("active_ids", []), list) else [])
        if str(mid)
    }
    state = read_provider_runtime_state()
    model_state = state.get("provider_model_state", {}) if isinstance(state.get("provider_model_state"), dict) else {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        model_id = str(candidate.get("id", "") or "")
        row = model_state.get(_provider_model_key("openrouter", model_id), {}) if isinstance(model_state.get(_provider_model_key("openrouter", model_id), {}), dict) else {}
        counts = _refresh_failure_window_fields(row)
        promotion_state = str(row.get("promotion_state", candidate.get("promotion_state", "candidate")) or "candidate").lower()
        if model_id in active_ids and promotion_state not in {"retired", "quarantined"}:
            promotion_state = "active"
        candidate["failure_count"] = int(row.get("failure_count", 0) or candidate.get("failure_count", 0) or 0)
        candidate["failure_count_24h"] = int(counts.get("failure_count_24h", 0) or 0)
        candidate["failure_count_7d"] = int(counts.get("failure_count_7d", 0) or 0)
        candidate["last_success_ts"] = row.get("last_success_ts")
        candidate["last_failure_ts"] = row.get("last_failure_ts")
        candidate["last_error_type"] = row.get("last_error_type")
        candidate["last_error_message"] = row.get("last_error_message")
        candidate["last_error_status_code"] = row.get("last_error_status_code")
        candidate["last_error_provider_code"] = row.get("last_error_provider_code")
        candidate["last_error_provider_type"] = row.get("last_error_provider_type")
        candidate["promotion_state"] = promotion_state
        candidate["health_status"] = _openrouter_health_status(row)
        transitions = _prune_promotion_transitions(row.get("promotion_transitions", []))
        smoke_checks = _prune_smoke_checks(row.get("smoke_checks", []))
        last_smoke = row.get("last_smoke_check") if isinstance(row.get("last_smoke_check"), dict) else (smoke_checks[-1] if smoke_checks else None)
        candidate["promotion_transition_count"] = len(transitions)
        candidate["recent_promotion_transitions"] = transitions[-5:]
        candidate["smoke_check_count"] = len(smoke_checks)
        candidate["recent_smoke_checks"] = smoke_checks[-5:]
        candidate["last_smoke_check"] = last_smoke
        candidate["lifecycle_evidence"] = {
            "promotion_state": promotion_state,
            "promotion_transition_count": len(transitions),
            "smoke_check_count": len(smoke_checks),
            "last_smoke_check": last_smoke,
            "last_failure_ts": row.get("last_failure_ts"),
            "last_error_type": row.get("last_error_type"),
        }
        candidate["activation_eligible"] = (
            not bool(row.get("disabled_until_manual_review", False))
            and not bool(row.get("exclude_from_free_rotation", False))
            and promotion_state not in {"quarantined", "retired"}
        )
        candidate["score"] = _score_openrouter_candidate(candidate)


def _is_model_in_cooldown(provider: str, model: str) -> bool:
    state = read_provider_runtime_state()
    model_state = state.get("provider_model_state", {})
    if not isinstance(model_state, dict):
        return False
    row = model_state.get(_provider_model_key(provider, model), {})
    if not isinstance(row, dict):
        return False
    cooldown_until = float(row.get("cooldown_until", 0) or 0)
    return cooldown_until > time.time()


@_state_transactional
def _mark_provider_success(provider: str, model: str):
    state = read_provider_runtime_state()
    model_state = state.get("provider_model_state", {})
    if not isinstance(model_state, dict):
        model_state = {}
    key = _provider_model_key(provider, model)
    row = model_state.get(key, {})
    if not isinstance(row, dict):
        row = {}
    now_iso = datetime.utcnow().isoformat() + "Z"
    row["last_success_ts"] = now_iso
    row["failure_count"] = 0
    row["last_error_type"] = None
    row["last_error_status_code"] = None
    row["last_error_provider_code"] = None
    row["last_error_provider_type"] = None
    row["cooldown_until"] = 0
    current_state = str(row.get("promotion_state", "") or "")
    if current_state != "retired":
        row["disabled_until_manual_review"] = False
    if current_state not in {"retired", "quarantined"}:
        row["exclude_from_free_rotation"] = False
    _refresh_failure_window_fields(row)
    if provider == "openrouter" and current_state not in {"retired", "quarantined"}:
        upstream_free = _openrouter_upstream_free_ids()
        is_free_candidate = bool(model in upstream_free or str(model).endswith(":free"))
        if is_free_candidate:
            _set_promotion_state_on_row(row, "active", reason="provider_success", at_ts=now_iso)
    model_state[key] = row
    state["provider_model_state"] = model_state
    write_provider_runtime_state(state)


def _openrouter_cooldown_seconds(err_type: str, retryable: bool, openrouter_cfg: dict) -> int:
    cfg = openrouter_cfg if isinstance(openrouter_cfg, dict) else {}

    def _cfg_seconds(key: str, default: int) -> int:
        raw = cfg.get(key, default)
        if isinstance(raw, bool):
            return default
        try:
            value = int(raw)
        except Exception:
            return default
        return max(0, value)

    if err_type == "rate_limited":
        return _cfg_seconds("cooldown_seconds_rate_limited", 30)
    if err_type == "quota_exhausted":
        return _cfg_seconds("cooldown_seconds_quota_exhausted", 300)
    if err_type == "not_free_anymore":
        return _cfg_seconds("cooldown_seconds_not_free_anymore", 900)
    if err_type == "model_unavailable":
        return _cfg_seconds("cooldown_seconds_model_unavailable", 120)
    if err_type in {"provider_error", "provider_timeout"}:
        return _cfg_seconds("cooldown_seconds_provider_error", 20)
    if retryable:
        return _cfg_seconds("cooldown_seconds_retryable_default", 10)
    return 0


@_state_transactional
def _mark_provider_failure(provider: str, model: str, normalized_error: dict):
    state = read_provider_runtime_state()
    model_state = state.get("provider_model_state", {})
    if not isinstance(model_state, dict):
        model_state = {}
    key = _provider_model_key(provider, model)
    row = model_state.get(key, {})
    if not isinstance(row, dict):
        row = {}
    failures = int(row.get("failure_count", 0) or 0) + 1
    err_type = str(normalized_error.get("type", "provider_error") or "provider_error").strip().lower()
    retryable = bool(normalized_error.get("retryable", False))
    status_code = normalized_error.get("status_code")
    provider_code = normalized_error.get("provider_code")
    provider_type = normalized_error.get("provider_type")
    now_iso = datetime.utcnow().isoformat() + "Z"

    events = row.get("failure_events", []) if isinstance(row.get("failure_events"), list) else []
    events.append({
        "ts": now_iso,
        "type": err_type,
        "retryable": retryable,
        "message": str(normalized_error.get("message", "") or ""),
        "status_code": status_code,
        "provider_code": provider_code,
        "provider_type": provider_type,
    })
    row["failure_events"] = events

    policies = read_provider_policies()
    openrouter_cfg = policies.get("openrouter", {}) if isinstance(policies.get("openrouter"), dict) else {}

    cooldown_seconds = 0
    if provider == "openrouter":
        cooldown_seconds = _openrouter_cooldown_seconds(err_type, retryable, openrouter_cfg)
    else:
        if err_type == "rate_limited":
            cooldown_seconds = 30
        elif err_type in ("quota_exhausted", "not_free_anymore"):
            cooldown_seconds = 300
        elif retryable:
            cooldown_seconds = 10

    row["failure_count"] = failures
    row["last_failure_ts"] = now_iso
    row["last_error_type"] = err_type
    row["last_error_message"] = str(normalized_error.get("message", ""))
    row["last_error_status_code"] = status_code
    row["last_error_provider_code"] = provider_code
    row["last_error_provider_type"] = provider_type
    row["cooldown_until"] = time.time() + cooldown_seconds if cooldown_seconds else 0
    counts = _refresh_failure_window_fields(row)

    def _cfg_int(raw_value, default_value: int, minimum: int = 0) -> int:
        if isinstance(raw_value, bool):
            return default_value
        try:
            value = int(raw_value)
        except Exception:
            return default_value
        return value if value >= minimum else minimum

    quarantine_24h = _cfg_int(openrouter_cfg.get("quarantine_failure_count_24h", 6), 6, 1)
    retire_7d = _cfg_int(openrouter_cfg.get("retire_failure_count_7d", 20), 20, 1)
    auth_manual_threshold = _cfg_int(openrouter_cfg.get("auth_error_manual_review_threshold", 1), 1, 1)

    if auth_manual_threshold < 1:
        auth_manual_threshold = 1

    if provider == "openrouter":
        if err_type == "auth_error" and failures >= auth_manual_threshold:
            row["disabled_until_manual_review"] = True
            row["exclude_from_free_rotation"] = True
            _set_promotion_state_on_row(row, "quarantined", reason="auth_error_manual_review", at_ts=now_iso)
        elif err_type == "model_unavailable" and failures >= 3:
            row["disabled_until_manual_review"] = True
            _set_promotion_state_on_row(row, "quarantined", reason="model_unavailable_threshold", at_ts=now_iso)

        if err_type in ("not_free_anymore", "quota_exhausted"):
            row["exclude_from_free_rotation"] = True
            if err_type == "not_free_anymore":
                _set_promotion_state_on_row(row, "retired", reason=err_type, at_ts=now_iso)
            else:
                _set_promotion_state_on_row(row, "quarantined", reason=err_type, at_ts=now_iso)
    else:
        if err_type in ("auth_error", "model_unavailable") and failures >= 3:
            row["disabled_until_manual_review"] = True

    if provider == "openrouter" and int(counts.get("failure_count_24h", 0) or 0) >= quarantine_24h:
        row["exclude_from_free_rotation"] = True
        _set_promotion_state_on_row(row, "quarantined", reason="failure_window_24h", at_ts=now_iso)

    if provider == "openrouter" and int(counts.get("failure_count_7d", 0) or 0) >= retire_7d:
        row["exclude_from_free_rotation"] = True
        _set_promotion_state_on_row(row, "retired", reason="failure_window_7d", at_ts=now_iso)

    model_state[key] = row
    state["provider_model_state"] = model_state
    write_provider_runtime_state(state)


@_state_transactional
def _consume_openrouter_free_token(policies: dict) -> tuple[bool, dict]:
    state = read_provider_runtime_state()
    limits = state.get("provider_rate_limits", {})
    if not isinstance(limits, dict):
        limits = {}
    pool = limits.get("openrouter_free", {})
    if not isinstance(pool, dict):
        pool = {}

    cfg = policies.get("openrouter", {}) if isinstance(policies.get("openrouter", {}), dict) else {}
    rpm_limit = int(cfg.get("free_rate_limit_rpm", pool.get("rpm_limit", 20)) or 20)
    window_seconds = int(pool.get("window_seconds", 60) or 60)
    now = int(time.time())
    window_start = int(pool.get("window_start", 0) or 0)
    request_count = int(pool.get("request_count", 0) or 0)

    if window_start <= 0 or now - window_start >= window_seconds:
        window_start = now
        request_count = 0

    allowed = request_count < rpm_limit
    if allowed:
        request_count += 1

    pool.update({
        "rpm_limit": rpm_limit,
        "window_seconds": window_seconds,
        "window_start": window_start,
        "request_count": request_count,
    })
    limits["openrouter_free"] = pool
    state["provider_rate_limits"] = limits
    write_provider_runtime_state(state)

    return allowed, {
        "rpm_limit": rpm_limit,
        "window_seconds": window_seconds,
        "window_start": window_start,
        "request_count": request_count,
    }


def _free_queue_priority(raw: str | None) -> tuple[str, int]:
    priority = str(raw or "batch").strip().lower()
    if priority not in FREE_QUEUE_PRIORITY_ORDER:
        priority = "batch"
    return priority, int(FREE_QUEUE_PRIORITY_ORDER[priority])


def _queue_entry_priority_rank(entry: dict) -> int:
    if not isinstance(entry, dict):
        return 999
    if entry.get("priority_rank") is not None:
        try:
            return int(entry.get("priority_rank"))
        except Exception:
            pass
    _, rank = _free_queue_priority(str(entry.get("priority", "batch")))
    return rank


def _queue_deadline_ms(entry: dict) -> int:
    try:
        return int(entry.get("deadline_ms", 0) or 0) if isinstance(entry, dict) else 0
    except (TypeError, ValueError):
        return 0


@_state_transactional
def _enqueue_free_tier_request(request_class: str, strategy: str, metadata: dict, policies: dict) -> tuple[bool, dict]:
    state = read_provider_runtime_state()
    queue = state.get("provider_request_queue", [])
    if not isinstance(queue, list):
        queue = []

    cfg = policies.get("openrouter", {}) if isinstance(policies.get("openrouter", {}), dict) else {}
    max_depth = max(1, int(cfg.get("max_queue_depth", 100) or 100))
    max_wait_ms = min(60000, max(1, int(cfg.get("max_queue_wait_ms", 15000) or 15000)))
    now_ms = int(time.time() * 1000)
    queue = [row for row in queue if isinstance(row, dict) and _queue_deadline_ms(row) >= now_ms]

    metadata_obj = metadata if isinstance(metadata, dict) else {}
    priority_raw = metadata_obj.get("priority") if isinstance(metadata_obj.get("priority"), str) else None
    priority, priority_rank = _free_queue_priority(priority_raw)

    dropped_entry = None
    if len(queue) >= max_depth:
        worst_idx = None
        worst_rank = -1
        worst_enqueue = ""
        for i, row in enumerate(queue):
            if not isinstance(row, dict):
                continue
            row_rank = _queue_entry_priority_rank(row)
            row_enqueue = str(row.get("enqueue_ts", ""))
            if row_rank > worst_rank or (row_rank == worst_rank and row_enqueue > worst_enqueue):
                worst_rank = row_rank
                worst_enqueue = row_enqueue
                worst_idx = i
        if worst_idx is not None and priority_rank < worst_rank:
            dropped_entry = queue.pop(worst_idx)

    if len(queue) >= max_depth:
        return False, {
            "max_queue_depth": max_depth,
            "queue_depth": len(queue),
            "max_queue_wait_ms": max_wait_ms,
            "priority": priority,
        }

    entry = {
        "id": uuid.uuid4().hex[:12],
        "request_class": request_class,
        "strategy": strategy,
        "priority": priority,
        "priority_rank": priority_rank,
        "enqueue_ts": datetime.utcnow().isoformat() + "Z",
        "enqueue_ms": now_ms,
        "deadline_ms": now_ms + max_wait_ms,
        "state": "queued",
    }
    queue.append(entry)
    queue.sort(key=lambda row: (_queue_entry_priority_rank(row), str(row.get("enqueue_ts", ""))))
    state["provider_request_queue"] = queue
    write_provider_runtime_state(state)
    payload = {
        "entry_id": entry["id"],
        "queue_depth": len(queue),
        "max_queue_depth": max_depth,
        "max_queue_wait_ms": max_wait_ms,
        "priority": priority,
    }
    if isinstance(dropped_entry, dict):
        payload["dropped_entry"] = {
            "id": dropped_entry.get("id"),
            "priority": dropped_entry.get("priority", "batch"),
        }
    return True, payload


@_state_transactional
def _claim_queued_free_tier_request(entry_id: str, policies: dict) -> tuple[str, dict]:
    """Atomically claim free-tier capacity for the highest-priority queued request."""
    state = read_provider_runtime_state()
    queue = state.get("provider_request_queue", [])
    if not isinstance(queue, list):
        queue = []

    now_ms = int(time.time() * 1000)
    live_queue = [
        row for row in queue
        if isinstance(row, dict) and _queue_deadline_ms(row) >= now_ms
    ]
    live_queue.sort(key=lambda row: (_queue_entry_priority_rank(row), str(row.get("enqueue_ts", ""))))
    own = next((row for row in live_queue if str(row.get("id", "")) == entry_id), None)
    if own is None:
        state["provider_request_queue"] = live_queue
        write_provider_runtime_state(state)
        return "expired_or_evicted", {"entry_id": entry_id, "queue_depth": len(live_queue)}

    position = next(i for i, row in enumerate(live_queue) if str(row.get("id", "")) == entry_id)
    if position != 0:
        if live_queue != queue:
            state["provider_request_queue"] = live_queue
            write_provider_runtime_state(state)
        return "waiting", {"entry_id": entry_id, "queue_depth": len(live_queue), "position": position + 1}

    cfg = policies.get("openrouter", {}) if isinstance(policies.get("openrouter", {}), dict) else {}
    limits = state.get("provider_rate_limits", {})
    if not isinstance(limits, dict):
        limits = {}
    pool = limits.get("openrouter_free", {})
    if not isinstance(pool, dict):
        pool = {}

    rpm_limit = int(cfg.get("free_rate_limit_rpm", pool.get("rpm_limit", 20)) or 20)
    window_seconds = int(pool.get("window_seconds", 60) or 60)
    now = int(time.time())
    window_start = int(pool.get("window_start", 0) or 0)
    request_count = int(pool.get("request_count", 0) or 0)
    if window_start <= 0 or now - window_start >= window_seconds:
        window_start = now
        request_count = 0

    limiter = {
        "rpm_limit": rpm_limit,
        "window_seconds": window_seconds,
        "window_start": window_start,
        "request_count": request_count,
    }
    if request_count >= rpm_limit:
        pool.update(limiter)
        limits["openrouter_free"] = pool
        state["provider_rate_limits"] = limits
        state["provider_request_queue"] = live_queue
        write_provider_runtime_state(state)
        return "waiting", {
            "entry_id": entry_id,
            "queue_depth": len(live_queue),
            "position": 1,
            "limiter": limiter,
        }

    request_count += 1
    limiter["request_count"] = request_count
    pool.update(limiter)
    limits["openrouter_free"] = pool
    state["provider_rate_limits"] = limits
    state["provider_request_queue"] = [
        row for row in live_queue if str(row.get("id", "")) != entry_id
    ]
    write_provider_runtime_state(state)
    return "acquired", {
        "entry_id": entry_id,
        "queue_depth": len(state["provider_request_queue"]),
        "waited_ms": max(0, now_ms - int(own.get("enqueue_ms", now_ms) or now_ms)),
        "limiter": limiter,
    }


@_state_transactional
def _remove_queued_free_tier_request(entry_id: str) -> bool:
    state = read_provider_runtime_state()
    queue = state.get("provider_request_queue", [])
    if not isinstance(queue, list):
        return False
    remaining = [row for row in queue if not isinstance(row, dict) or str(row.get("id", "")) != entry_id]
    if len(remaining) == len(queue):
        return False
    state["provider_request_queue"] = remaining
    write_provider_runtime_state(state)
    return True


def _wait_for_queued_free_tier_request(queue_info: dict, policies: dict) -> tuple[bool, dict]:
    entry_id = str(queue_info.get("entry_id", ""))
    max_wait_ms = max(0, int(queue_info.get("max_queue_wait_ms", 0) or 0))
    deadline = time.monotonic() + (max_wait_ms / 1000.0)
    last = {"entry_id": entry_id, "queue_depth": queue_info.get("queue_depth", 0)}
    while True:
        status, detail = _claim_queued_free_tier_request(entry_id, policies)
        last = detail
        if status == "acquired":
            return True, {**queue_info, **detail, "state": "acquired"}
        if status == "expired_or_evicted":
            return False, {**queue_info, **detail, "state": status}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _remove_queued_free_tier_request(entry_id)
            return False, {**queue_info, **last, "state": "timed_out", "waited_ms": max_wait_ms}
        time.sleep(min(0.1, remaining))


def _handle_free_tier_overflow(
    request_class: str,
    strategy: str,
    allow_fallbacks: bool,
    metadata: dict,
    limiter: dict,
    policies: dict,
) -> tuple[str, dict]:
    cfg = policies.get("openrouter", {}) if isinstance(policies.get("openrouter", {}), dict) else {}
    behavior = str(cfg.get("queue_behavior", "wait"))
    metadata_obj = metadata if isinstance(metadata, dict) else {}
    if "priority" not in metadata_obj:
        metadata_obj = dict(metadata_obj)
        metadata_obj["priority"] = "interactive" if request_class in {"chat", "completion"} else "batch"

    base_error = {
        "type": "protective_rate_limited",
        "message": "OpenRouter free-tier local limiter engaged",
        "retryable": True,
        "limiter": limiter,
        "queue_behavior": behavior,
    }

    if behavior == "wait":
        ok, q = _enqueue_free_tier_request(request_class, strategy, metadata_obj, policies)
        if ok:
            acquired, queue_result = _wait_for_queued_free_tier_request(q, policies)
            base_error["queue"] = queue_result
            if acquired:
                return "acquired", base_error
            base_error["type"] = "queue_timeout"
            base_error["message"] = "Timed out waiting for free-tier capacity"
            if allow_fallbacks:
                return "continue", base_error
            return "break", base_error
        base_error["type"] = "queue_full"
        base_error["message"] = "Free-tier queue is full"
        base_error["queue"] = q
        if allow_fallbacks:
            return "continue", base_error
        return "break", base_error

    if behavior == "fail_fast":
        return "break", base_error

    if behavior in ("fallback_to_local", "upgrade_to_paid"):
        return "continue", base_error

    # Unknown behavior: conservative fail fast.
    base_error["message"] = f"Unknown queue_behavior '{behavior}'"
    return "break", base_error


def _resolve_project_id(req) -> str | None:
    project_id = str(getattr(req, "project_id", "") or "").strip()
    if project_id:
        return project_id
    metadata = getattr(req, "metadata", {})
    if not isinstance(metadata, dict):
        return None
    for key in ("project_id", "project", "caller_project"):
        raw = str(metadata.get(key, "") or "").strip()
        if raw:
            return raw
    return None


def _normalize_task_type(task_type: str | None) -> str:
    task = str(task_type or "chat").strip().lower()
    return task if task in POLICY_TASK_TYPES else "chat"


def _project_override_for_request(req, policies: dict) -> dict:
    overrides = policies.get("project_overrides", {}) if isinstance(policies.get("project_overrides"), dict) else {}
    project_id = _resolve_project_id(req)
    if not project_id:
        return {}
    row = overrides.get(project_id, {})
    return row if isinstance(row, dict) else {}


def _task_override_for_request(req, policy_scope: dict) -> dict:
    task_overrides = policy_scope.get("task_overrides", {}) if isinstance(policy_scope.get("task_overrides"), dict) else {}
    task_type = _normalize_task_type(getattr(req, "task_type", "chat"))
    row = task_overrides.get(task_type, {})
    return row if isinstance(row, dict) else {}


def _merge_selection_map(base: dict, patch: dict):
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return
    for key, value in patch.items():
        if isinstance(value, list):
            base[str(key)] = list(value)


def _effective_policy_defaults(req, policies: dict) -> dict:
    defaults = dict(policies.get("defaults", {}) if isinstance(policies.get("defaults"), dict) else {})
    root_task_override = _task_override_for_request(req, policies)
    root_task_defaults = root_task_override.get("defaults", {}) if isinstance(root_task_override.get("defaults"), dict) else {}
    defaults.update(root_task_defaults)
    override = _project_override_for_request(req, policies)
    override_defaults = override.get("defaults", {}) if isinstance(override.get("defaults"), dict) else {}
    defaults.update(override_defaults)
    project_task_override = _task_override_for_request(req, override)
    project_task_defaults = project_task_override.get("defaults", {}) if isinstance(project_task_override.get("defaults"), dict) else {}
    defaults.update(project_task_defaults)
    return defaults


def _effective_policy_selection(req, policies: dict) -> dict:
    selection = dict(policies.get("selection", {}) if isinstance(policies.get("selection"), dict) else {})
    root_task_override = _task_override_for_request(req, policies)
    _merge_selection_map(selection, root_task_override.get("selection", {}))
    override = _project_override_for_request(req, policies)
    _merge_selection_map(selection, override.get("selection", {}))
    project_task_override = _task_override_for_request(req, override)
    _merge_selection_map(selection, project_task_override.get("selection", {}))
    return selection


def _normalize_strategy_name(raw_strategy: str | None) -> tuple[str, bool]:
    strategy = str(raw_strategy or "").strip()
    if strategy in ROUTING_STRATEGIES:
        return strategy, True
    return "local_first", False


def _strategy_value_and_source(req, env: dict, policies: dict) -> tuple[str, str]:
    req_strategy = str(req.provider_preferences.strategy or "").strip()
    if req_strategy and req_strategy != "default":
        return req_strategy, "request.provider_preferences.strategy"

    project_override = _project_override_for_request(req, policies)
    project_task_override = _task_override_for_request(req, project_override)
    project_task_defaults = project_task_override.get("defaults", {}) if isinstance(project_task_override.get("defaults"), dict) else {}
    project_task_strategy = str(project_task_defaults.get("strategy", "") or "").strip()
    if project_task_strategy:
        return project_task_strategy, "project_overrides.task_overrides.defaults.strategy"

    project_defaults = project_override.get("defaults", {}) if isinstance(project_override.get("defaults"), dict) else {}
    project_strategy = str(project_defaults.get("strategy", "") or "").strip()
    if project_strategy:
        return project_strategy, "project_overrides.defaults.strategy"

    root_task_override = _task_override_for_request(req, policies)
    root_task_defaults = root_task_override.get("defaults", {}) if isinstance(root_task_override.get("defaults"), dict) else {}
    root_task_strategy = str(root_task_defaults.get("strategy", "") or "").strip()
    if root_task_strategy:
        return root_task_strategy, "task_overrides.defaults.strategy"

    defaults = policies.get("defaults", {}) if isinstance(policies.get("defaults"), dict) else {}
    default_strategy = str(defaults.get("strategy", "") or "").strip()
    if default_strategy:
        return default_strategy, "defaults.strategy"

    env_strategy = str(env.get("DEFAULT_ROUTING_STRATEGY", "") or "").strip()
    if env_strategy:
        return env_strategy, "env.DEFAULT_ROUTING_STRATEGY"

    return "local_first", "builtin.local_first"


def _strategy_resolution_context(req, env: dict, policies: dict) -> dict:
    requested, strategy_source = _strategy_value_and_source(req, env, policies)
    resolved, strategy_valid = _normalize_strategy_name(requested)
    return {
        "requested_strategy": str(requested or "").strip() or "local_first",
        "resolved_strategy": resolved,
        "strategy_source": strategy_source,
        "strategy_valid": strategy_valid,
        "strategy_fallback_applied": not strategy_valid,
        "strategy_fallback_reason": None if strategy_valid else "invalid_strategy_value",
    }


def _resolve_strategy(req, env: dict, policies: dict) -> str:
    return str(_strategy_resolution_context(req, env, policies).get("resolved_strategy", "local_first"))


def _resolve_strategy_source(req, env: dict, policies: dict) -> str:
    return str(_strategy_resolution_context(req, env, policies).get("strategy_source", "builtin.local_first"))


def _normalize_candidate_chain(raw_chain: list[str] | None) -> list[str]:
    rows = raw_chain if isinstance(raw_chain, list) else []
    out = []
    seen = set()
    for lane in rows:
        normalized = str(lane or "").strip()
        if normalized not in ROUTING_LANES:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _build_route_policy_context(
    req,
    env: dict,
    policies: dict,
    strategy: str,
    candidate_chain: list[str],
    effective_defaults: dict | None = None,
    effective_selection: dict | None = None,
    strategy_resolution: dict | None = None,
    chain_resolution: dict | None = None,
) -> dict:
    project_override = _project_override_for_request(req, policies)
    root_task_override = _task_override_for_request(req, policies)
    project_task_override = _task_override_for_request(req, project_override)

    defaults = effective_defaults if isinstance(effective_defaults, dict) else _effective_policy_defaults(req, policies)
    selection = effective_selection if isinstance(effective_selection, dict) else _effective_policy_selection(req, policies)
    strategy_ctx = strategy_resolution if isinstance(strategy_resolution, dict) else _strategy_resolution_context(req, env, policies)
    chain_ctx = (
        chain_resolution
        if isinstance(chain_resolution, dict)
        else _candidate_chain_resolution(
            strategy,
            req,
            policies,
            effective_defaults=defaults,
            effective_selection=selection,
        )
    )

    return {
        "task_type": _normalize_task_type(getattr(req, "task_type", "chat")),
        "project_id": _resolve_project_id(req),
        "strategy_source": strategy_ctx.get("strategy_source"),
        "requested_strategy": strategy_ctx.get("requested_strategy"),
        "resolved_strategy": strategy_ctx.get("resolved_strategy"),
        "strategy_valid": bool(strategy_ctx.get("strategy_valid", False)),
        "strategy_fallback_applied": bool(strategy_ctx.get("strategy_fallback_applied", False)),
        "strategy_fallback_reason": strategy_ctx.get("strategy_fallback_reason"),
        "project_override_applied": bool(project_override),
        "global_task_override_applied": bool(root_task_override),
        "project_task_override_applied": bool(project_task_override),
        "effective_defaults": defaults,
        "effective_selection": selection,
        "candidate_chain": list(candidate_chain if isinstance(candidate_chain, list) else []),
        "candidate_chain_source": chain_ctx.get("chain_source"),
        "candidate_chain_before_filters": list(chain_ctx.get("normalized_chain", [])),
        "candidate_chain_after_filters": list(
            chain_ctx.get("filtered_chain_pre_ranking", chain_ctx.get("filtered_chain", []))
        ),
        "candidate_chain_after_ranking": list(chain_ctx.get("filtered_chain", [])),
        "filter_flags": {
            "free_only": bool(chain_ctx.get("free_only", False)),
            "paid_allowed": bool(chain_ctx.get("paid_allowed", True)),
        },
        "dynamic_ranking": {
            "enabled": bool(chain_ctx.get("dynamic_ranking_enabled", False)),
            "applied": bool(chain_ctx.get("dynamic_ranking_applied", False)),
            "reason": chain_ctx.get("dynamic_ranking_reason"),
            "ranked_chain": list(chain_ctx.get("filtered_chain", [])),
            "rows": list(chain_ctx.get("dynamic_ranking_rows", [])),
        },
        "strict_provider_target": chain_ctx.get("strict_provider_target"),
        "strict_provider_task_allowed_lanes": list(chain_ctx.get("strict_provider_task_allowed_lanes", [])),
        "strict_provider_task_constraint_applied": bool(chain_ctx.get("strict_provider_task_constraint_applied", False)),
        "strict_provider_task_constraint_reason": chain_ctx.get("strict_provider_task_constraint_reason"),
        "strict_provider_filter_relaxed": bool(chain_ctx.get("strict_provider_filter_relaxed", False)),
        "service_tier": chain_ctx.get("service_tier"),
        "service_tier_requested": chain_ctx.get("service_tier_requested"),
        "service_tier_source": chain_ctx.get("service_tier_source"),
        "service_tier_chain": list(chain_ctx.get("service_tier_chain", [])),
    }


def _resolve_bool_pref(req_val: bool | None, default_val: bool) -> bool:
    if req_val is None:
        return default_val
    return bool(req_val)


def _normalize_service_tier(raw_tier: str | None) -> str:
    tier = str(raw_tier or "").strip().lower()
    if tier in ROUTING_SERVICE_TIERS:
        return tier
    return "default"


def _normalize_preferred_provider_lane(raw_provider: str | None) -> str:
    preferred = str(raw_provider or "").strip().lower()
    if preferred in {"openrouter.free", "openrouter.paid", "openrouter", "openai", "local"}:
        return preferred
    return "local"


def _service_tier_chain(req, policies: dict, effective_defaults: dict | None = None) -> dict:
    defaults = effective_defaults if isinstance(effective_defaults, dict) else _effective_policy_defaults(req, policies)
    req_tier = str(getattr(req.provider_preferences, "service_tier", "") or "").strip().lower()
    default_tier = str(defaults.get("service_tier", "default") or "default").strip().lower()
    tier_source = "request.provider_preferences.service_tier"
    selected = req_tier
    if not selected or selected == "default":
        selected = default_tier
        tier_source = "effective_defaults.service_tier"
    normalized_tier = _normalize_service_tier(selected)
    if normalized_tier == "default" and selected not in {"", "default"}:
        tier_source = "invalid_service_tier_fallback"

    tiers = policies.get("service_tiers", {}) if isinstance(policies.get("service_tiers"), dict) else {}
    row = tiers.get(normalized_tier, {}) if isinstance(tiers.get(normalized_tier), dict) else {}
    chain = _normalize_candidate_chain(row.get("chain", []) if isinstance(row.get("chain"), list) else [])
    return {
        "requested": selected or "default",
        "service_tier": normalized_tier,
        "source": tier_source,
        "chain": chain,
        "applied": normalized_tier != "default" and bool(chain),
    }


def _dynamic_ranking_policy(policies: dict) -> dict:
    defaults = (
        DEFAULT_PROVIDER_POLICIES.get("dynamic_ranking", {})
        if isinstance(DEFAULT_PROVIDER_POLICIES.get("dynamic_ranking", {}), dict)
        else {}
    )
    raw = policies.get("dynamic_ranking", {}) if isinstance(policies.get("dynamic_ranking", {}), dict) else {}

    def _to_non_negative_float(value, fallback: float) -> float:
        if isinstance(value, bool):
            return fallback
        try:
            parsed = float(value)
        except Exception:
            return fallback
        return parsed if parsed >= 0 else fallback

    def _to_int(value, fallback: int, minimum: int) -> int:
        if isinstance(value, bool):
            return fallback
        try:
            parsed = int(value)
        except Exception:
            return fallback
        return parsed if parsed >= minimum else minimum

    enabled = bool(raw.get("enabled", defaults.get("enabled", True)))

    strategies_raw = raw.get("strategies", defaults.get("strategies", []))
    if not isinstance(strategies_raw, list):
        strategies_raw = defaults.get("strategies", [])
    strategies = [
        str(strategy)
        for strategy in strategies_raw
        if str(strategy) in ROUTING_STRATEGIES
    ]
    if not strategies:
        strategies = [
            str(strategy)
            for strategy in (defaults.get("strategies", []) if isinstance(defaults.get("strategies", []), list) else [])
            if str(strategy) in ROUTING_STRATEGIES
        ]

    default_weights = defaults.get("weights", {}) if isinstance(defaults.get("weights", {}), dict) else {}
    weights_raw = raw.get("weights", {}) if isinstance(raw.get("weights", {}), dict) else {}
    cost_weight = _to_non_negative_float(weights_raw.get("cost", default_weights.get("cost", 0.6)), 0.6)
    availability_weight = _to_non_negative_float(
        weights_raw.get("availability", default_weights.get("availability", 0.3)),
        0.3,
    )
    quality_weight = _to_non_negative_float(weights_raw.get("quality", default_weights.get("quality", 0.1)), 0.1)
    weight_total = cost_weight + availability_weight + quality_weight
    if weight_total <= 0:
        cost_weight, availability_weight, quality_weight = 0.6, 0.3, 0.1
        weight_total = 1.0

    token_defaults = defaults.get("token_estimate", {}) if isinstance(defaults.get("token_estimate", {}), dict) else {}
    token_raw = raw.get("token_estimate", {}) if isinstance(raw.get("token_estimate", {}), dict) else {}
    prompt_tokens = _to_int(token_raw.get("prompt_tokens", token_defaults.get("prompt_tokens", 500)), 500, 1)
    completion_tokens = _to_int(
        token_raw.get("completion_tokens", token_defaults.get("completion_tokens", 256)),
        256,
        0,
    )

    unknown_cost_score_raw = raw.get("unknown_cost_score", defaults.get("unknown_cost_score", 0.35))
    if isinstance(unknown_cost_score_raw, bool):
        unknown_cost_score = 0.35
    else:
        try:
            unknown_cost_score = float(unknown_cost_score_raw)
        except Exception:
            unknown_cost_score = 0.35
    if unknown_cost_score < 0:
        unknown_cost_score = 0.0
    if unknown_cost_score > 1:
        unknown_cost_score = 1.0

    return {
        "enabled": enabled,
        "strategies": strategies,
        "weights": {
            "cost": cost_weight / weight_total,
            "availability": availability_weight / weight_total,
            "quality": quality_weight / weight_total,
        },
        "token_estimate": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
        "unknown_cost_score": unknown_cost_score,
    }


def _lane_quality_score(lane: str) -> float:
    lookup = {
        "openai": 1.0,
        "openrouter.paid": 0.85,
        "local": 0.75,
        "openrouter.free": 0.55,
    }
    return float(lookup.get(str(lane), 0.5))


def _lane_availability_score(
    lane: str,
    provider: str,
    model_id: str,
    policies: dict,
    runtime_state: dict,
) -> float:
    score = 1.0
    state = runtime_state if isinstance(runtime_state, dict) else {}

    provider_state = state.get("provider_model_state", {}) if isinstance(state.get("provider_model_state", {}), dict) else {}
    row_key = _provider_model_key(provider, model_id)
    row = provider_state.get(row_key, {}) if isinstance(provider_state.get(row_key), dict) else {}
    now_ts = time.time()

    if row:
        if float(row.get("cooldown_until", 0) or 0) > now_ts:
            score -= 0.45
        if bool(row.get("disabled_until_manual_review", False)):
            score -= 0.60
        if bool(row.get("exclude_from_free_rotation", False)):
            score -= 0.35
        promotion_state = str(row.get("promotion_state", "") or "").lower()
        if promotion_state in {"quarantined", "retired"}:
            score -= 0.70
        failures_24h = int(row.get("failure_count_24h", 0) or 0)
        if failures_24h > 0:
            score -= min(0.35, failures_24h * 0.06)
        err_type = str(row.get("last_error_type", "") or "").strip().lower()
        if err_type in {"auth_error", "provider_timeout", "model_unavailable"}:
            score -= 0.10

    if lane == "openrouter.free":
        limits = state.get("provider_rate_limits", {}) if isinstance(state.get("provider_rate_limits", {}), dict) else {}
        pool = limits.get("openrouter_free", {}) if isinstance(limits.get("openrouter_free", {}), dict) else {}
        openrouter_cfg = policies.get("openrouter", {}) if isinstance(policies.get("openrouter", {}), dict) else {}

        rpm_limit = int(pool.get("rpm_limit", openrouter_cfg.get("free_rate_limit_rpm", 20)) or 20)
        request_count = int(pool.get("request_count", 0) or 0)
        if rpm_limit > 0:
            utilization = min(1.0, float(request_count) / float(rpm_limit))
            score -= 0.25 * utilization

        queue = state.get("provider_request_queue", []) if isinstance(state.get("provider_request_queue", []), list) else []
        max_depth = int(openrouter_cfg.get("max_queue_depth", 100) or 100)
        if max_depth > 0:
            queue_ratio = min(1.0, float(len(queue)) / float(max_depth))
            score -= 0.20 * queue_ratio

    return max(0.0, min(1.0, score))


def _rank_candidate_chain(
    candidate_chain: list[str],
    req,
    provider_models: dict,
    policies: dict,
    ranking_policy: dict,
) -> tuple[list[str], list[dict]]:
    lanes = [str(lane) for lane in candidate_chain if str(lane)]
    if not lanes:
        return [], []

    provider_catalog = provider_models if isinstance(provider_models, dict) else {}
    runtime_state = read_provider_runtime_state()
    weights = ranking_policy.get("weights", {}) if isinstance(ranking_policy.get("weights", {}), dict) else {}
    cost_weight = float(weights.get("cost", 0.6) or 0.6)
    availability_weight = float(weights.get("availability", 0.3) or 0.3)
    quality_weight = float(weights.get("quality", 0.1) or 0.1)
    unknown_cost_score = float(ranking_policy.get("unknown_cost_score", 0.35) or 0.35)

    token_estimate = ranking_policy.get("token_estimate", {}) if isinstance(ranking_policy.get("token_estimate", {}), dict) else {}
    prompt_tokens = int(token_estimate.get("prompt_tokens", 500) or 500)
    completion_tokens = int(token_estimate.get("completion_tokens", 256) or 256)
    max_tokens = getattr(req, "max_tokens", None)
    if max_tokens is not None:
        try:
            completion_tokens = max(0, int(max_tokens))
        except Exception:
            pass
    if _normalize_task_type(getattr(req, "task_type", "chat")) == "embed":
        completion_tokens = 0

    rows = []
    for index, lane in enumerate(lanes):
        provider, model_id = _pick_catalog_model(
            lane,
            provider_catalog,
            req,
            excluded_models=set(),
            policies=policies,
        )
        estimated_cost_usd = _estimate_request_cost_usd(
            provider_catalog,
            provider,
            model_id,
            prompt_tokens,
            completion_tokens,
        )
        availability_score = _lane_availability_score(
            lane,
            provider,
            model_id,
            policies,
            runtime_state,
        )
        quality_score = _lane_quality_score(lane)
        rows.append({
            "lane": lane,
            "provider": provider,
            "model": model_id,
            "base_order": index + 1,
            "estimated_cost_usd": estimated_cost_usd,
            "availability_score": round(float(availability_score), 6),
            "quality_score": round(float(quality_score), 6),
        })

    known_costs = [
        float(row.get("estimated_cost_usd"))
        for row in rows
        if row.get("estimated_cost_usd") is not None
    ]
    min_cost = min(known_costs) if known_costs else None
    max_cost = max(known_costs) if known_costs else None

    for row in rows:
        est_cost = row.get("estimated_cost_usd")
        if est_cost is None:
            cost_score = unknown_cost_score
        else:
            cost_value = float(est_cost)
            if min_cost is None or max_cost is None or max_cost <= min_cost:
                cost_score = 1.0
            else:
                cost_score = 1.0 - ((cost_value - min_cost) / (max_cost - min_cost))
        cost_score = max(0.0, min(1.0, float(cost_score)))
        row["cost_score"] = round(cost_score, 6)
        weighted = (
            (cost_weight * cost_score)
            + (availability_weight * float(row.get("availability_score", 0.0) or 0.0))
            + (quality_weight * float(row.get("quality_score", 0.0) or 0.0))
        )
        row["weighted_score"] = round(float(weighted), 6)

    ranked_rows = sorted(
        rows,
        key=lambda row: (
            -float(row.get("weighted_score", 0.0) or 0.0),
            int(row.get("base_order", 9999) or 9999),
            str(row.get("lane", "")),
        ),
    )
    for idx, row in enumerate(ranked_rows):
        row["rank"] = idx + 1

    ranked_chain = [str(row.get("lane", "")) for row in ranked_rows if str(row.get("lane", ""))]
    return ranked_chain, ranked_rows


def _candidate_chain_resolution(
    strategy: str,
    req,
    policies: dict,
    effective_defaults: dict | None = None,
    effective_selection: dict | None = None,
    provider_models: dict | None = None,
) -> dict:
    defaults = effective_defaults if isinstance(effective_defaults, dict) else _effective_policy_defaults(req, policies)
    selection = effective_selection if isinstance(effective_selection, dict) else _effective_policy_selection(req, policies)
    task_type = _normalize_task_type(getattr(req, "task_type", "chat"))

    free_only = _resolve_bool_pref(req.provider_preferences.free_only, bool(defaults.get("free_only", False)))
    paid_allowed = _resolve_bool_pref(req.provider_preferences.paid_allowed, bool(defaults.get("paid_allowed", True)))

    strict_target = None
    strict_task_constraint_applied = False
    strict_task_constraint_reason = None
    strict_task_allowed_lanes = sorted(list(STRICT_PROVIDER_TASK_ALLOWED_LANES.get(task_type, ROUTING_LANES)))
    strict_filter_relaxed = False

    tier_ctx = _service_tier_chain(req, policies, effective_defaults=defaults)

    if strategy == "strict_provider":
        strict_target = _normalize_preferred_provider_lane(
            req.provider_preferences.preferred_provider or defaults.get("preferred_provider", "local")
        )
        if strict_target == "openrouter":
            raw_chain = ["openrouter.free", "openrouter.paid"]
        elif strict_target in {"openrouter.free", "openrouter.paid", "openai", "local"}:
            raw_chain = [strict_target]
        else:
            raw_chain = ["local"]
        chain_source = f"strict_provider.{strict_target}"

        if task_type == "embed" and strict_target in {"openrouter", "openrouter.free"}:
            raw_chain = ["openrouter.paid"]
            strict_task_constraint_applied = True
            strict_task_constraint_reason = "embed_openrouter_paid_only"

        task_allowed = STRICT_PROVIDER_TASK_ALLOWED_LANES.get(task_type, ROUTING_LANES)
        task_filtered_chain = [lane for lane in raw_chain if lane in task_allowed]
        if task_filtered_chain != raw_chain and not strict_task_constraint_reason:
            strict_task_constraint_applied = True
            strict_task_constraint_reason = f"task_type_lane_filter.{task_type}"
        if task_filtered_chain:
            raw_chain = task_filtered_chain

        if strict_task_constraint_applied:
            chain_source = f"{chain_source}.task_{task_type}"
    else:
        if bool(tier_ctx.get("applied", False)):
            raw_chain = tier_ctx.get("chain", [])
            chain_source = f"service_tiers.{tier_ctx.get('service_tier')}.chain"
        else:
            fallback_chain = DEFAULT_PROVIDER_POLICIES.get("selection", {}).get(strategy, ["local"])
            if strategy in selection and isinstance(selection.get(strategy), list):
                raw_chain = selection.get(strategy, fallback_chain)
                chain_source = f"effective_selection.{strategy}"
            else:
                raw_chain = fallback_chain
                chain_source = f"default_selection.{strategy}"

    normalized_chain = _normalize_candidate_chain(raw_chain if isinstance(raw_chain, list) else ["local"])
    if not normalized_chain:
        normalized_chain = ["local"]

    filtered = []
    for lane in normalized_chain:
        if free_only and lane in {"openrouter.paid", "openai"}:
            continue
        if not paid_allowed and lane in {"openrouter.paid", "openai"}:
            continue
        filtered.append(lane)
    filtered_chain = _normalize_candidate_chain(filtered)
    if not filtered_chain:
        if strategy == "strict_provider":
            filtered_chain = _normalize_candidate_chain(normalized_chain) or ["local"]
            strict_filter_relaxed = True
            if not strict_task_constraint_reason:
                strict_task_constraint_reason = "strict_provider_filter_relaxed"
        else:
            filtered_chain = ["local"]

    filtered_chain_pre_ranking = list(filtered_chain)
    ranking_policy = _dynamic_ranking_policy(policies)
    dynamic_reason = "policy_disabled"
    ranking_rows = []

    if len(filtered_chain_pre_ranking) <= 1:
        dynamic_reason = "chain_too_short"
    elif strategy == "strict_provider":
        dynamic_reason = "strict_provider_locked"
    elif bool(tier_ctx.get("applied", False)):
        dynamic_reason = "service_tier_chain_locked"
    elif not bool(ranking_policy.get("enabled", False)):
        dynamic_reason = "policy_disabled"
    elif strategy not in set(ranking_policy.get("strategies", [])):
        dynamic_reason = "strategy_not_enabled"
    else:
        provider_models_doc = provider_models if isinstance(provider_models, dict) else read_provider_models()
        ranked_chain, ranking_rows = _rank_candidate_chain(
            filtered_chain_pre_ranking,
            req,
            provider_models_doc,
            policies,
            ranking_policy,
        )
        if ranked_chain:
            filtered_chain = _normalize_candidate_chain(ranked_chain) or filtered_chain_pre_ranking
            dynamic_reason = "ranked"
        else:
            dynamic_reason = "ranking_no_candidates"

    dynamic_applied = filtered_chain != filtered_chain_pre_ranking

    return {
        "strategy": strategy,
        "task_type": task_type,
        "chain_source": chain_source,
        "normalized_chain": normalized_chain,
        "filtered_chain_pre_ranking": filtered_chain_pre_ranking,
        "filtered_chain": filtered_chain,
        "free_only": free_only,
        "paid_allowed": paid_allowed,
        "dynamic_ranking_enabled": bool(ranking_policy.get("enabled", False)),
        "dynamic_ranking_applied": dynamic_applied,
        "dynamic_ranking_reason": dynamic_reason,
        "dynamic_ranking_rows": ranking_rows,
        "strict_provider_target": strict_target,
        "strict_provider_task_allowed_lanes": strict_task_allowed_lanes,
        "strict_provider_task_constraint_applied": strict_task_constraint_applied,
        "strict_provider_task_constraint_reason": strict_task_constraint_reason,
        "strict_provider_filter_relaxed": strict_filter_relaxed,
        "service_tier": tier_ctx.get("service_tier"),
        "service_tier_requested": tier_ctx.get("requested"),
        "service_tier_source": tier_ctx.get("source"),
        "service_tier_chain": tier_ctx.get("chain", []),
    }


def _candidate_chain_for_strategy(strategy: str, req, policies: dict) -> list[str]:
    resolution = _candidate_chain_resolution(strategy, req, policies)
    chain = resolution.get("filtered_chain", []) if isinstance(resolution, dict) else []
    return list(chain if isinstance(chain, list) and chain else ["local"])


def _append_route_attempt(
    attempts: list[dict],
    lane: str,
    provider: str,
    model: str,
    result: str,
    reason_code: str,
    error: dict | None = None,
    fallback_action: str | None = None,
):
    row = {
        "attempt_index": len(attempts) + 1,
        "lane": str(lane or ""),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "result": str(result or ""),
        "reason_code": str(reason_code or "unknown"),
    }
    if isinstance(error, dict):
        row["error"] = error
    if fallback_action:
        row["fallback_action"] = str(fallback_action)
    attempts.append(row)


def _normalized_reason_fragment(raw: str | None) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return "provider_error"
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "provider_error"


def _dispatch_reason_code(normalized_error: dict) -> str:
    err_type = ""
    if isinstance(normalized_error, dict):
        err_type = str(normalized_error.get("type", "") or "")
    return f"dispatch_{_normalized_reason_fragment(err_type)}"


def _provider_block_reason(provider: str, normalized_error: dict) -> str | None:
    provider_name = str(provider or "").strip().lower()
    err_type = str((normalized_error or {}).get("type", "") or "").strip().lower()
    if err_type == "auth_error" and provider_name in {"openrouter", "openai"}:
        return "auth_error"
    return None


def _build_fallback_summary(
    candidate_chain: list[str],
    attempt_trace: list[dict],
    allow_fallbacks: bool,
    selected_lane: str | None,
    selected_provider: str | None,
    selected_model: str | None,
) -> dict:
    attempts = [row for row in attempt_trace if isinstance(row, dict)]
    selected_attempt_index = None
    reason_codes = []
    attempted_lanes = []
    attempted_providers = []
    blocked_or_skipped_count = 0
    dispatch_error_count = 0

    for row in attempts:
        result = str(row.get("result", "") or "")
        if result == "selected" and selected_attempt_index is None:
            try:
                selected_attempt_index = int(row.get("attempt_index", 0) or 0)
            except Exception:
                selected_attempt_index = None

        lane = str(row.get("lane", "") or "")
        if lane and lane not in attempted_lanes:
            attempted_lanes.append(lane)

        provider = str(row.get("provider", "") or "")
        if provider and provider not in attempted_providers:
            attempted_providers.append(provider)

        reason_code = str(row.get("reason_code", "") or "")
        if reason_code and reason_code not in reason_codes:
            reason_codes.append(reason_code)

        if result in {"blocked", "skipped"}:
            blocked_or_skipped_count += 1
        elif result == "error":
            dispatch_error_count += 1

    used_fallback = bool(selected_attempt_index is not None and selected_attempt_index > 1)
    return {
        "allow_fallbacks": bool(allow_fallbacks),
        "chain_length": len(candidate_chain if isinstance(candidate_chain, list) else []),
        "attempt_count": len(attempts),
        "selected_attempt_index": selected_attempt_index,
        "selected_on_first_attempt": bool(selected_attempt_index == 1),
        "used_fallback": used_fallback,
        "selected_lane": selected_lane,
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "attempted_lanes": attempted_lanes,
        "attempted_providers": attempted_providers,
        "attempt_reason_codes": reason_codes,
        "blocked_or_skipped_count": blocked_or_skipped_count,
        "dispatch_error_count": dispatch_error_count,
    }


def _openrouter_upstream_free_ids() -> set[str]:
    state = read_provider_runtime_state()
    cache = state.get("openrouter_catalog_cache", {}) if isinstance(state.get("openrouter_catalog_cache"), dict) else {}
    free_ids = cache.get("free_ids", []) if isinstance(cache.get("free_ids", []), list) else []
    return {str(mid) for mid in free_ids if str(mid)}


def _catalog_entry(provider_models: dict, provider: str, model: str) -> dict | None:
    entries = _catalog_entries_by_provider(provider_models, provider)
    row = next((e for e in entries if str(e.get("id", "")) == str(model)), None)
    return row if isinstance(row, dict) else None


def _estimate_request_cost_usd(provider_models: dict, provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    if provider not in ("openrouter", "openai"):
        return 0.0
    entry = _catalog_entry(provider_models, provider, model)
    if not isinstance(entry, dict):
        return None
    in_cost, out_cost = _extract_pricing(entry)
    if in_cost is None and out_cost is None:
        return None
    total = 0.0
    if in_cost is not None:
        total += (max(0.0, float(prompt_tokens)) / 1000.0) * in_cost
    if out_cost is not None:
        total += (max(0.0, float(completion_tokens)) / 1000.0) * out_cost
    return round(total, 8)


@_state_transactional
def _record_spend(
    provider: str,
    lane: str,
    model: str,
    amount_usd: float,
    request_id: str,
    strategy: str,
    policies: dict | None = None,
):
    if amount_usd is None:
        return
    amount = float(amount_usd)
    if amount <= 0:
        return

    retention = _retention_policy_settings(policies)
    state = read_provider_runtime_state()
    budget_state = state.get("budget_state", {}) if isinstance(state.get("budget_state"), dict) else {}
    daily = budget_state.get("daily", {}) if isinstance(budget_state.get("daily"), dict) else {}
    monthly = budget_state.get("monthly", {}) if isinstance(budget_state.get("monthly"), dict) else {}

    now = datetime.utcnow()
    day_key = now.strftime("%Y-%m-%d")
    month_key = now.strftime("%Y-%m")

    day_row = daily.get(day_key, {"total_usd": 0.0, "by_provider": {}})
    mon_row = monthly.get(month_key, {"total_usd": 0.0, "by_provider": {}})
    if not isinstance(day_row, dict):
        day_row = {"total_usd": 0.0, "by_provider": {}}
    if not isinstance(mon_row, dict):
        mon_row = {"total_usd": 0.0, "by_provider": {}}

    day_row["total_usd"] = round(float(day_row.get("total_usd", 0.0) or 0.0) + amount, 8)
    mon_row["total_usd"] = round(float(mon_row.get("total_usd", 0.0) or 0.0) + amount, 8)

    day_bp = day_row.get("by_provider", {}) if isinstance(day_row.get("by_provider"), dict) else {}
    mon_bp = mon_row.get("by_provider", {}) if isinstance(mon_row.get("by_provider"), dict) else {}
    day_bp[provider] = round(float(day_bp.get(provider, 0.0) or 0.0) + amount, 8)
    mon_bp[provider] = round(float(mon_bp.get(provider, 0.0) or 0.0) + amount, 8)
    day_row["by_provider"] = day_bp
    mon_row["by_provider"] = mon_bp

    daily[day_key] = day_row
    monthly[month_key] = mon_row

    # Keep recent windows only.
    daily_keep = retention["budget_daily_history_days"]
    monthly_keep = retention["budget_monthly_history_months"]
    if len(daily) > daily_keep:
        for k in sorted(daily.keys())[:-daily_keep]:
            daily.pop(k, None)
    if len(monthly) > monthly_keep:
        for k in sorted(monthly.keys())[:-monthly_keep]:
            monthly.pop(k, None)

    lifetime = round(float(budget_state.get("lifetime_total_usd", 0.0) or 0.0) + amount, 8)
    budget_state["daily"] = daily
    budget_state["monthly"] = monthly
    budget_state["lifetime_total_usd"] = lifetime
    budget_state["last_spend_ts"] = now.isoformat() + "Z"
    state["budget_state"] = budget_state

    spend_logs = state.get("spend_logs", []) if isinstance(state.get("spend_logs"), list) else []
    spend_logs.append({
        "ts": now.isoformat() + "Z",
        "request_id": request_id,
        "provider": provider,
        "lane": lane,
        "model": model,
        "strategy": strategy,
        "amount_usd": amount,
    })
    state["spend_logs"] = spend_logs[-retention["spend_logs_max"]:]
    write_provider_runtime_state(state)


def _budget_snapshot(policies: dict) -> dict:
    budget = policies.get("budget", {}) if isinstance(policies.get("budget"), dict) else {}
    state = read_provider_runtime_state()
    bstate = state.get("budget_state", {}) if isinstance(state.get("budget_state"), dict) else {}
    daily = bstate.get("daily", {}) if isinstance(bstate.get("daily"), dict) else {}
    monthly = bstate.get("monthly", {}) if isinstance(bstate.get("monthly"), dict) else {}
    today = datetime.utcnow().strftime("%Y-%m-%d")
    this_month = datetime.utcnow().strftime("%Y-%m")
    day_total = float((daily.get(today, {}) or {}).get("total_usd", 0.0) or 0.0)
    month_total = float((monthly.get(this_month, {}) or {}).get("total_usd", 0.0) or 0.0)
    return {
        "today_key": today,
        "month_key": this_month,
        "day_total_usd": round(day_total, 8),
        "month_total_usd": round(month_total, 8),
        "daily_limit_usd": float(budget.get("daily_usd_limit", 0.0) or 0.0),
        "monthly_limit_usd": float(budget.get("monthly_usd_limit", 0.0) or 0.0),
        "per_request_limit_usd": float(budget.get("per_request_usd_limit", 0.0) or 0.0),
        "warn_threshold_pct": float(budget.get("warn_threshold_pct", 0.8) or 0.8),
    }


def _enforce_budget_guardrail(
    policies: dict,
    provider: str,
    lane: str,
    model: str,
    provider_models: dict,
    req_max_tokens: int | None,
):
    budget = policies.get("budget", {}) if isinstance(policies.get("budget"), dict) else {}
    enabled = budget.get("providers", {}) if isinstance(budget.get("providers"), dict) else {}
    if not bool(enabled.get(provider, False)):
        return

    snap = _budget_snapshot(policies)
    hard_fail = bool(budget.get("hard_fail_on_budget_exceeded", True))
    daily_limit = float(budget.get("daily_usd_limit", 0.0) or 0.0)
    monthly_limit = float(budget.get("monthly_usd_limit", 0.0) or 0.0)
    per_request_limit = float(budget.get("per_request_usd_limit", 0.0) or 0.0)

    if daily_limit > 0 and snap["day_total_usd"] >= daily_limit:
        if hard_fail:
            raise HTTPException(429, {
                "message": "Daily budget exceeded for paid provider routing",
                "provider": provider,
                "lane": lane,
                "budget": snap,
            })
    if monthly_limit > 0 and snap["month_total_usd"] >= monthly_limit:
        if hard_fail:
            raise HTTPException(429, {
                "message": "Monthly budget exceeded for paid provider routing",
                "provider": provider,
                "lane": lane,
                "budget": snap,
            })

    if per_request_limit > 0:
        est = _estimate_request_cost_usd(
            provider_models,
            provider,
            model,
            prompt_tokens=500,
            completion_tokens=int(req_max_tokens or 256),
        )
        if est is not None and float(est) > per_request_limit and hard_fail:
            raise HTTPException(429, {
                "message": "Per-request budget guardrail exceeded",
                "provider": provider,
                "lane": lane,
                "model": model,
                "estimated_request_usd": est,
                "budget": snap,
            })


def _request_capability_requirements(req) -> dict:
    task_type = _normalize_task_type(getattr(req, "task_type", "chat"))
    requirements = {
        "task": {
            "chat": "chat",
            "completion": "completions",
            "embed": "embeddings",
        }.get(task_type, "chat"),
        "tools": bool(getattr(req, "tools", None)),
        "structured_outputs": bool(getattr(req, "json_schema", None)),
        "reasoning": False,
        "vision": False,
    }
    model_prefs = getattr(req, "model_preferences", None)
    preferred_tags = []
    if model_prefs is not None and isinstance(getattr(model_prefs, "preferred_model_tags", None), list):
        preferred_tags = [str(tag).strip().lower() for tag in model_prefs.preferred_model_tags if str(tag).strip()]
    for tag in preferred_tags:
        if tag in {"tools", "tool_calling", "function_calling"}:
            requirements["tools"] = True
        elif tag in {"structured", "structured_outputs", "json_schema", "response_format"}:
            requirements["structured_outputs"] = True
        elif tag in {"reasoning", "cot"}:
            requirements["reasoning"] = True
        elif tag in {"vision", "multimodal"}:
            requirements["vision"] = True
    return requirements


def _row_supports_requirements(row: dict, requirements: dict) -> bool:
    if not isinstance(row, dict):
        return False
    reqs = requirements if isinstance(requirements, dict) else {}
    raw_capabilities = row.get("capabilities", []) if isinstance(row.get("capabilities"), list) else []
    capabilities = {
        str(capability).strip().lower()
        for capability in raw_capabilities
        if str(capability).strip()
    }

    if str(reqs.get("task", "")) not in capabilities:
        return False
    if bool(reqs.get("tools", False)) and "tool_calling" not in capabilities:
        return False
    if bool(reqs.get("structured_outputs", False)) and "structured_output" not in capabilities:
        return False
    if bool(reqs.get("reasoning", False)) and "reasoning" not in capabilities:
        return False
    if bool(reqs.get("vision", False)) and "vision" not in capabilities:
        return False
    return True


def _local_slot_supports_request(row: dict, req) -> bool:
    capabilities = {
        str(cap).strip().lower()
        for cap in (row.get("capabilities", []) if isinstance(row.get("capabilities", []), list) else [])
        if str(cap).strip()
    }
    required_task_capability = {
        "chat": "chat",
        "completion": "completions",
        "embed": "embeddings",
    }.get(_normalize_task_type(getattr(req, "task_type", "chat")), "chat")
    if required_task_capability not in capabilities:
        return False

    requirements = _request_capability_requirements(req)
    mapping = {
        "tools": {"tool_calling"},
        "structured_outputs": {"structured_output"},
        "reasoning": {"reasoning"},
        "vision": {"vision", "multimodal"},
    }
    for requirement, accepted in mapping.items():
        if bool(requirements.get(requirement, False)) and not capabilities.intersection(accepted):
            return False
    return True


def _constrain_chain_to_preferred_model(req, chain: list[str], provider_models: dict, policies: dict) -> list[str]:
    preferred = str(getattr(getattr(req, "model_preferences", None), "preferred_model", "") or "").strip()
    if not preferred:
        return chain

    requirements = _request_capability_requirements(req)
    matching_lanes: list[str] = []

    local = provider_models.get("local", {}) if isinstance(provider_models.get("local", {}), dict) else {}
    for row in local.get("slots", []) if isinstance(local.get("slots", []), list) else []:
        if not isinstance(row, dict) or not bool(row.get("enabled", True)):
            continue
        mode = _normalize_slot_mode(row.get("id"), default="")
        aliases = {mode, _slot_alias_for_mode(mode), _active_model_name_for_mode(mode)}
        if preferred in aliases and _local_slot_supports_request(row, req):
            matching_lanes.append("local")
            break

    openrouter = provider_models.get("openrouter", {}) if isinstance(provider_models.get("openrouter", {}), dict) else {}
    for bucket, lane in (("free", "openrouter.free"), ("paid", "openrouter.paid")):
        rows = openrouter.get(bucket, []) if isinstance(openrouter.get(bucket, []), list) else []
        if any(
            isinstance(row, dict)
            and bool(row.get("enabled", True))
            and str(row.get("id", "")) == preferred
            and _row_supports_requirements(row, requirements)
            for row in rows
        ):
            matching_lanes.append(lane)

    openai = provider_models.get("openai", {}) if isinstance(provider_models.get("openai", {}), dict) else {}
    allowed = openai.get("allowed", []) if isinstance(openai.get("allowed", []), list) else []
    if any(
        isinstance(row, dict)
        and bool(row.get("enabled", True))
        and str(row.get("id", "")) == preferred
        and _row_supports_requirements(row, requirements)
        for row in allowed
    ):
        matching_lanes.append("openai")

    manual = _manual_openrouter_free_candidates_state()
    if preferred in {str(mid) for mid in manual.get("active_ids", []) if str(mid)}:
        state = read_provider_runtime_state()
        cache = state.get("openrouter_catalog_cache", {}) if isinstance(state.get("openrouter_catalog_cache", {}), dict) else {}
        row = _openrouter_catalog_model_map(cache).get(preferred, {})
        if (
            isinstance(row, dict)
            and bool(row.get("is_free", False))
            and _row_supports_requirements(row, requirements)
            and _openrouter_state_allows_free_rotation(preferred)
        ):
            matching_lanes.append("openrouter.free")

    matching_lanes = list(dict.fromkeys(matching_lanes))
    selected = next((lane for lane in chain if lane in matching_lanes), None)
    if selected is None:
        raise HTTPException(422, {
            "message": "preferred_model is not an enabled, capability-compatible model in the permitted candidate chain",
            "preferred_model": preferred,
            "candidate_chain": chain,
            "catalog_lanes": matching_lanes,
        })
    return [selected]


def _openrouter_state_allows_free_rotation(model_id: str) -> bool:
    if not str(model_id or "").strip():
        return False
    row = _provider_model_state_row("openrouter", model_id)
    if bool(row.get("exclude_from_free_rotation", False)):
        return False
    if bool(row.get("disabled_until_manual_review", False)):
        return False
    if str(row.get("promotion_state", "") or "").lower() in {"quarantined", "retired"}:
        return False
    return True


def _pick_catalog_model(
    lane: str,
    provider_models: dict,
    req: RouterChatRequest,
    excluded_models: set[str] | None = None,
    policies: dict | None = None,
) -> tuple[str, str]:
    def _priority_sort_key(row: dict) -> tuple[int, str]:
        try:
            priority = int(row.get("priority", 9999) or 9999)
        except Exception:
            priority = 9999
        model_id = str(row.get("id", "") or "")
        return (priority, model_id)

    def _apply_capability_filter(rows: list[dict], requirements: dict) -> list[dict]:
        return [row for row in rows if _row_supports_requirements(row, requirements)]

    excluded_models = excluded_models or set()
    policies = policies or {}
    requirements = _request_capability_requirements(req)
    preferred = (req.model_preferences.preferred_model or "").strip()

    if lane == "local":
        local_cfg = provider_models.get("local", {}) if isinstance(provider_models.get("local", {}), dict) else {}
        slot_rows = local_cfg.get("slots", []) if isinstance(local_cfg.get("slots", []), list) else []

        preferred_tags: list[str] = []
        model_prefs = getattr(req, "model_preferences", None)
        if model_prefs is not None and isinstance(getattr(model_prefs, "preferred_model_tags", None), list):
            preferred_tags = [str(tag).strip().lower() for tag in model_prefs.preferred_model_tags if str(tag).strip()]

        task_type = _normalize_task_type(getattr(req, "task_type", "chat"))
        required_capability = {
            "chat": "chat",
            "completion": "completions",
            "embed": "embeddings",
        }.get(task_type, "chat")

        slot_backends = read_slot_backends()
        candidates: list[dict] = []
        for row in slot_rows:
            if not isinstance(row, dict):
                continue
            slot_mode = _normalize_slot_mode(row.get("id"), default="")
            if slot_mode not in SLOT_MODES:
                continue
            if not bool(row.get("enabled", True)):
                continue

            capabilities_raw = row.get("capabilities", []) if isinstance(row.get("capabilities", []), list) else []
            capabilities = {str(cap).strip().lower() for cap in capabilities_raw if str(cap).strip()}
            alias = _slot_alias_for_mode(slot_mode)
            if not alias or alias in excluded_models:
                continue

            runtime_backend = str(slot_backends.get(slot_mode, row.get("backend") or DEFAULT_SLOT_BACKENDS.get(slot_mode, "tgw")) or "").strip().lower()
            if runtime_backend not in SUPPORTED_BACKENDS:
                runtime_backend = DEFAULT_SLOT_BACKENDS.get(slot_mode, "tgw")

            try:
                priority = int(row.get("priority", 9999) or 9999)
            except Exception:
                priority = 9999

            candidates.append({
                "slot_mode": slot_mode,
                "alias": alias,
                "active_model": _active_model_name_for_mode(slot_mode),
                "backend": runtime_backend,
                "capabilities": capabilities,
                "priority": priority,
                "source_row": row,
            })

        if candidates:
            pool = [c for c in candidates if _local_slot_supports_request(c["source_row"], req)]
            if preferred:
                pool = [
                    c for c in pool
                    if preferred in {c["slot_mode"], c["alias"], c.get("active_model")}
                ]
            if not pool:
                raise HTTPException(422, {
                    "message": "no enabled local slot satisfies the requested model and capabilities",
                    "preferred_model": preferred or None,
                    "required_capability": required_capability,
                })

            backend_hints = [tag for tag in preferred_tags if tag in SUPPORTED_BACKENDS]
            if backend_hints:
                backend_filtered = [c for c in pool if c["backend"] in backend_hints]
                if backend_filtered:
                    pool = backend_filtered

            mode_hints: list[str] = []
            for tag in preferred_tags:
                if tag in SLOT_MODES:
                    mode_hints.append(tag)
                elif tag in {"util"}:
                    mode_hints.append("small")
                elif tag in {"classification", "classify"}:
                    mode_hints.append("intent")
                elif tag in {"embed", "embedding", "embeddings", "vector"}:
                    mode_hints.append("embed")

            mode_order: dict[str, int] = {}
            for idx, mode_hint in enumerate(mode_hints):
                if mode_hint not in mode_order:
                    mode_order[mode_hint] = idx
            if task_type == "embed":
                default_mode_rank = {"embed": 0, "small": 1, "chat": 2, "intent": 3}
            else:
                default_mode_rank = {"chat": 0, "intent": 1, "small": 2, "embed": 3}

            pool.sort(key=lambda c: (
                0 if c["slot_mode"] in mode_order else 1,
                mode_order.get(c["slot_mode"], default_mode_rank.get(c["slot_mode"], 99)),
                int(c["priority"]),
                str(c["alias"]),
            ))
            return "local", str(pool[0]["alias"])

        raise HTTPException(422, "no enabled local slots are configured")

    if lane == "openrouter.free":
        items = provider_models.get("openrouter", {}).get("free", [])
        enabled = [m for m in items if isinstance(m, dict) and m.get("enabled", True)]
        if bool((policies.get("openrouter", {}) if isinstance(policies.get("openrouter", {}), dict) else {}).get("enforce_upstream_free_status", True)):
            upstream_free = _openrouter_upstream_free_ids()
            if upstream_free:
                enabled = [m for m in enabled if str(m.get("id", "")) in upstream_free]
        enabled = [m for m in enabled if str(m.get("id", "")) not in excluded_models]
        enabled = [m for m in enabled if _openrouter_state_allows_free_rotation(str(m.get("id", "") or ""))]
        enabled = _apply_capability_filter(enabled, requirements)
        if preferred:
            enabled = [m for m in enabled if str(m.get("id", "")) == preferred]
        enabled.sort(key=_priority_sort_key)
        if enabled:
            return "openrouter", str(enabled[0]["id"])

        manual_section = _manual_openrouter_free_candidates_state()
        active_ids = [str(mid) for mid in manual_section.get("active_ids", []) if str(mid)]
        catalog_state = read_provider_runtime_state()
        catalog = catalog_state.get("openrouter_catalog_cache", {}) if isinstance(catalog_state.get("openrouter_catalog_cache"), dict) else {}
        catalog_map = _openrouter_catalog_model_map(catalog)
        upstream_free = _openrouter_upstream_free_ids()
        for model_id in active_ids:
            if preferred and model_id != preferred:
                continue
            if model_id in excluded_models:
                continue
            row = catalog_map.get(model_id, {}) if isinstance(catalog_map.get(model_id, {}), dict) else {}
            if not bool(row.get("is_free", False)):
                continue
            if upstream_free and model_id not in upstream_free:
                continue
            if not _openrouter_state_allows_free_rotation(model_id):
                continue
            if not _row_supports_requirements(row, requirements):
                continue
            return "openrouter", model_id

        raise HTTPException(422, {
            "message": "no enabled OpenRouter free model satisfies the request",
            "preferred_model": preferred or None,
        })

    if lane == "openrouter.paid":
        items = provider_models.get("openrouter", {}).get("paid", [])
        enabled = [m for m in items if isinstance(m, dict) and m.get("enabled", True)]
        enabled = [m for m in enabled if str(m.get("id", "")) not in excluded_models]
        enabled = _apply_capability_filter(enabled, requirements)
        if preferred:
            enabled = [m for m in enabled if str(m.get("id", "")) == preferred]
        enabled.sort(key=_priority_sort_key)
        if not enabled:
            raise HTTPException(422, {
                "message": "no enabled OpenRouter paid model satisfies the request",
                "preferred_model": preferred or None,
            })
        return "openrouter", str(enabled[0]["id"])

    if lane == "openai":
        items = provider_models.get("openai", {}).get("allowed", [])
        enabled = [m for m in items if isinstance(m, dict) and m.get("enabled", True)]
        enabled = [m for m in enabled if str(m.get("id", "")) not in excluded_models]
        enabled = _apply_capability_filter(enabled, requirements)
        if preferred:
            enabled = [m for m in enabled if str(m.get("id", "")) == preferred]
        enabled.sort(key=_priority_sort_key)
        if not enabled:
            raise HTTPException(422, {
                "message": "no enabled OpenAI model satisfies the request",
                "preferred_model": preferred or None,
            })
        return "openai", str(enabled[0]["id"])

    raise HTTPException(422, f"unsupported routing lane: {lane}")


def _build_chat_payload(req: RouterChatRequest, selected_model: str, provider: str) -> dict:
    messages = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.extend([m.model_dump() for m in req.messages])
    payload = {
        "model": selected_model,
        "messages": messages,
    }
    if req.temperature is not None:
        payload["temperature"] = req.temperature
    if req.top_p is not None:
        payload["top_p"] = req.top_p
    if req.max_tokens is not None:
        payload["max_tokens"] = req.max_tokens
    if req.stop:
        payload["stop"] = req.stop
    if req.tools:
        payload["tools"] = req.tools
    if req.json_schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": req.provider_json_schema(),
        }
    if req.no_thinking and provider == "local":
        payload["enable_thinking"] = False
    return payload


def _build_completion_payload(req: RouterCompletionRequest, selected_model: str, provider: str) -> dict:
    payload = {
        "model": selected_model,
        "prompt": req.prompt,
    }
    if req.max_tokens is not None:
        payload["max_tokens"] = req.max_tokens
    if req.temperature is not None:
        payload["temperature"] = req.temperature
    if req.top_p is not None:
        payload["top_p"] = req.top_p
    if req.stop:
        payload["stop"] = req.stop
    if req.no_thinking and provider == "local":
        payload["enable_thinking"] = False
    return payload


def _build_embed_payload(req: RouterEmbedRequest, selected_model: str) -> dict:
    return {
        "model": selected_model,
        "input": req.input,
    }


def _dispatch_provider_chat(provider: str, env: dict, payload: dict, provider_models: dict | None = None) -> dict:
    if provider == "local":
        adapter = LocalProviderAdapter()
        local_endpoint = _local_endpoint_for_model(
            str(payload.get("model", "") or ""),
            env,
            provider_models=provider_models,
            fallback_mode="chat",
        )
        return adapter.chat({"base": local_endpoint["base"], "payload": payload})
    if provider == "openrouter":
        adapter = OpenRouterProviderAdapter()
        return adapter.chat({
            "base": env.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
            "api_key": env.get("OPENROUTER_API_KEY", ""),
            "payload": payload,
            "x_title": "llm-manager",
            "http_referer": "http://127.0.0.1/llm-manager",
        })
    if provider == "openai":
        adapter = OpenAIProviderAdapter()
        return adapter.chat({
            "base": env.get("OPENAI_API_BASE", "https://api.openai.com/v1"),
            "api_key": env.get("OPENAI_API_KEY", ""),
            "payload": payload,
        })
    raise RuntimeError(f"Unsupported provider: {provider}")


def _dispatch_provider_completions(provider: str, env: dict, payload: dict, provider_models: dict | None = None) -> dict:
    if provider == "local":
        adapter = LocalProviderAdapter()
        local_endpoint = _local_endpoint_for_model(
            str(payload.get("model", "") or ""),
            env,
            provider_models=provider_models,
            fallback_mode="chat",
        )
        return adapter.completions({"base": local_endpoint["base"], "payload": payload})
    if provider == "openrouter":
        adapter = OpenRouterProviderAdapter()
        return adapter.completions({
            "base": env.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
            "api_key": env.get("OPENROUTER_API_KEY", ""),
            "payload": payload,
            "x_title": "llm-manager",
            "http_referer": "http://127.0.0.1/llm-manager",
        })
    if provider == "openai":
        adapter = OpenAIProviderAdapter()
        return adapter.completions({
            "base": env.get("OPENAI_API_BASE", "https://api.openai.com/v1"),
            "api_key": env.get("OPENAI_API_KEY", ""),
            "payload": payload,
        })
    raise RuntimeError(f"Unsupported provider: {provider}")


def _dispatch_provider_embeddings(provider: str, env: dict, payload: dict, provider_models: dict | None = None) -> dict:
    if provider == "local":
        adapter = LocalProviderAdapter()
        local_endpoint = _local_endpoint_for_model(
            str(payload.get("model", "") or ""),
            env,
            provider_models=provider_models,
            fallback_mode="embed",
        )
        return adapter.embeddings({"base": local_endpoint["base"], "payload": payload})
    if provider == "openrouter":
        adapter = OpenRouterProviderAdapter()
        return adapter.embeddings({
            "base": env.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
            "api_key": env.get("OPENROUTER_API_KEY", ""),
            "payload": payload,
            "x_title": "llm-manager",
            "http_referer": "http://127.0.0.1/llm-manager",
        })
    if provider == "openai":
        adapter = OpenAIProviderAdapter()
        return adapter.embeddings({
            "base": env.get("OPENAI_API_BASE", "https://api.openai.com/v1"),
            "api_key": env.get("OPENAI_API_KEY", ""),
            "payload": payload,
        })
    raise RuntimeError(f"Unsupported provider: {provider}")


def _normalize_provider_error(provider: str, error: Exception) -> dict:
    if provider == "openrouter":
        return OpenRouterProviderAdapter().normalize_error(error)
    if provider == "openai":
        return OpenAIProviderAdapter().normalize_error(error)
    return LocalProviderAdapter().normalize_error(error)


class _VisibleTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in {"script", "style", "noscript", "svg", "path", "head"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript", "svg", "path", "head"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag in {"br", "div", "p", "section", "article", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "menu", "button", "a"}:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        if data:
            self.parts.append(data)


def _html_visible_lines(raw_html: str) -> list[str]:
    parser = _VisibleTextExtractor()
    parser.feed(raw_html)
    parser.close()
    text = "".join(parser.parts)
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", str(line or "")).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _parse_compact_number(value: str) -> float | None:
    raw = str(value or "").replace(",", "").strip().upper()
    if not raw:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)([KMBT])$", raw)
    if not m:
        return None
    number = float(m.group(1))
    scale = {
        "K": 1_000,
        "M": 1_000_000,
        "B": 1_000_000_000,
        "T": 1_000_000_000_000,
    }[m.group(2)]
    return number * scale


def _normalize_openrouter_name(value: str) -> str:
    lowered = str(value or "").lower().strip()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _normalize_openrouter_author(value: str) -> str:
    return _normalize_openrouter_name(value).replace(" ", "")


def _openrouter_name_variants(name: str) -> set[str]:
    raw = str(name or "").strip()
    variants = {_normalize_openrouter_name(raw)} if raw else set()
    if ":" in raw:
        variants.add(_normalize_openrouter_name(raw.split(":", 1)[1]))
    return {v for v in variants if v}


def _normalize_openrouter_category_key(value: str) -> str:
    return _normalize_openrouter_name(value).replace(" ", "_")


def _openrouter_ranking_bucket_from_line(line: str) -> str | None:
    normalized = _normalize_openrouter_name(line)
    if not normalized:
        return None
    if normalized in {"most popular", "popular", "most used"}:
        return "most_popular"
    if normalized in {"top weekly", "weekly", "this week"}:
        return "top_weekly"
    if normalized.startswith("category "):
        category = _normalize_openrouter_category_key(normalized[len("category "):])
        return f"category:{category}" if category else None
    if normalized.endswith(" models") and len(normalized.split()) <= 4:
        category = _normalize_openrouter_category_key(normalized[:-7])
        if category and category not in {"most_popular", "top_weekly"}:
            return f"category:{category}"
    return None


def _format_param_size(value: float | None) -> str | None:
    if value is None:
        return None
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _detect_openrouter_free(pricing: dict, model_id: str, model_name: str) -> tuple[bool, str | None]:
    prompt = str(pricing.get("prompt", "") or "")
    completion = str(pricing.get("completion", "") or "")
    try:
        if float(prompt or 0) == 0.0 and float(completion or 0) == 0.0:
            return True, "pricing_zero"
    except Exception:
        pass
    if str(model_id or "").endswith(":free"):
        return True, "id_suffix"
    if "(free)" in str(model_name or "").lower():
        return True, "name_suffix"
    return False, None


def _infer_openrouter_model_size(model_id: str, model_name: str, description: str) -> dict:
    sources = [
        ("high", str(model_id or "")),
        ("medium", str(model_name or "")),
        ("low", str(description or "")),
    ]
    total_params_b = None
    active_params_b = None
    confidence = "none"

    for source_confidence, text in sources:
        lower = text.lower()
        if active_params_b is None:
            active_match = re.search(r"\ba(\d+(?:\.\d+)?)b\b", lower)
            if active_match:
                active_params_b = float(active_match.group(1))
                confidence = source_confidence
        if total_params_b is None:
            total_matches = [float(m.group(1)) for m in re.finditer(r"(?<![a-z0-9])(\d+(?:\.\d+)?)b\b", lower)]
            if total_matches:
                total_params_b = max(total_matches)
                confidence = source_confidence
        if total_params_b is not None and active_params_b is not None:
            break

    size_parts = []
    formatted_total = _format_param_size(total_params_b)
    formatted_active = _format_param_size(active_params_b)
    if formatted_total:
        size_parts.append(f"{formatted_total}B")
    if formatted_active:
        size_parts.append(f"A{formatted_active}B")

    return {
        "estimated_total_params_b": total_params_b,
        "estimated_active_params_b": active_params_b,
        "size_confidence": confidence,
        "size_label": " ".join(size_parts) if size_parts else None,
    }


def _openrouter_rankings_lookup(entries: list[dict]) -> dict:
    lookup = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        author = _normalize_openrouter_author(entry.get("author", ""))
        name = _normalize_openrouter_name(entry.get("name", ""))
        if author and name:
            lookup[(author, name)] = entry
    return lookup


def _fetch_openrouter_rankings() -> dict:
    snapshot = {
        "rankings": [],
        "fetched_ts": datetime.utcnow().isoformat() + "Z",
        "error": None,
        "source": "https://openrouter.ai/rankings",
    }
    try:
        html = requests.get(snapshot["source"], timeout=20).text
        lines = _html_visible_lines(html)
        rows_by_key: dict[tuple[str, str], dict] = {}
        active_bucket = "most_popular"
        index = 0
        while index < len(lines):
            line = lines[index]
            bucket = _openrouter_ranking_bucket_from_line(line)
            if bucket is not None:
                active_bucket = bucket
                index += 1
                continue

            if (
                line.isdigit()
                and index + 6 < len(lines)
                and lines[index + 1] == "."
                and lines[index + 3].lower() == "by"
            ):
                rank = int(line)
                model_name = lines[index + 2]
                author_name = lines[index + 4]
                tokens_value = _parse_compact_number(lines[index + 5])
                key = (
                    _normalize_openrouter_author(author_name),
                    _normalize_openrouter_name(model_name),
                )
                if key[0] and key[1]:
                    row = rows_by_key.get(key, {
                        "name": model_name,
                        "author": author_name,
                        "popularity_rank": None,
                        "top_weekly_rank": None,
                        "category_ranks": {},
                        "popularity_tokens": None,
                    })
                    if tokens_value is not None:
                        prev_tokens = row.get("popularity_tokens")
                        if prev_tokens is None or float(tokens_value) > float(prev_tokens):
                            row["popularity_tokens"] = int(tokens_value)

                    if active_bucket == "top_weekly":
                        prev = row.get("top_weekly_rank")
                        if prev is None or rank < int(prev):
                            row["top_weekly_rank"] = rank
                    elif str(active_bucket).startswith("category:"):
                        category_key = str(active_bucket).split(":", 1)[1]
                        category_ranks = row.get("category_ranks", {}) if isinstance(row.get("category_ranks"), dict) else {}
                        prev = category_ranks.get(category_key)
                        if prev is None or rank < int(prev):
                            category_ranks[category_key] = rank
                        row["category_ranks"] = category_ranks
                    else:
                        prev = row.get("popularity_rank")
                        if prev is None or rank < int(prev):
                            row["popularity_rank"] = rank

                    rows_by_key[key] = row

                index += 7
                while index < len(lines):
                    if _openrouter_ranking_bucket_from_line(lines[index]) is not None:
                        break
                    if lines[index].isdigit() and index + 1 < len(lines) and lines[index + 1] == ".":
                        break
                    index += 1
                continue
            index += 1

        entries = list(rows_by_key.values())
        snapshot["rankings"] = entries
        if not entries:
            snapshot["error"] = "no rankings parsed from page"
    except Exception as e:
        snapshot["error"] = str(e)
    return snapshot


def _openrouter_ranking_for_model(model_id: str, model_name: str, rankings_lookup: dict) -> dict | None:
    author = _normalize_openrouter_author(str(model_id or "").split("/", 1)[0])
    for variant in _openrouter_name_variants(model_name):
        row = rankings_lookup.get((author, variant))
        if isinstance(row, dict):
            return row
    return None


def _openrouter_health_status(model_state_row: dict) -> str:
    row = model_state_row if isinstance(model_state_row, dict) else {}
    promotion_state = str(row.get("promotion_state", "") or "").lower()
    if promotion_state == "retired":
        return "retired"
    if promotion_state == "quarantined":
        return "quarantined"
    if bool(row.get("disabled_until_manual_review", False)):
        return "manual_review"
    if bool(row.get("exclude_from_free_rotation", False)):
        return "quarantined"
    if float(row.get("cooldown_until", 0) or 0) > time.time():
        return "cooldown"
    if int(row.get("failure_count_24h", 0) or 0) > 0:
        return "degraded"
    if int(row.get("failure_count", 0) or 0) > 0:
        return "degraded"
    if row.get("last_success_ts"):
        return "healthy"
    return "unknown"


def _build_openrouter_catalog_model(row: dict, rankings_lookup: dict) -> dict | None:
    if not isinstance(row, dict):
        return None
    model_id = str(row.get("id", "") or "").strip()
    if not model_id:
        return None
    model_name = str(row.get("name", "") or model_id)
    pricing = row.get("pricing", {}) if isinstance(row.get("pricing"), dict) else {}
    architecture = row.get("architecture", {}) if isinstance(row.get("architecture"), dict) else {}
    top_provider = row.get("top_provider", {}) if isinstance(row.get("top_provider"), dict) else {}
    supported_parameters = row.get("supported_parameters", []) if isinstance(row.get("supported_parameters"), list) else []
    output_modalities = architecture.get("output_modalities", []) if isinstance(architecture.get("output_modalities"), list) else []
    input_modalities = architecture.get("input_modalities", []) if isinstance(architecture.get("input_modalities"), list) else []
    is_free, free_detection_source = _detect_openrouter_free(pricing, model_id, model_name)
    created = row.get("created")
    created_ts = None
    try:
        if created is not None:
            created_ts = datetime.utcfromtimestamp(int(created)).isoformat() + "Z"
    except Exception:
        created_ts = None
    ranking_row = _openrouter_ranking_for_model(model_id, model_name, rankings_lookup)
    size_data = _infer_openrouter_model_size(model_id, model_name, str(row.get("description", "") or ""))
    normalized_outputs = {str(modality).strip().lower() for modality in output_modalities if str(modality).strip()}
    capabilities = set()
    if "embeddings" in normalized_outputs:
        capabilities.add("embeddings")
    if "text" in normalized_outputs or not normalized_outputs:
        capabilities.update({"chat", "completions"})
    if "tools" in supported_parameters:
        capabilities.add("tool_calling")
    if "structured_outputs" in supported_parameters or "response_format" in supported_parameters:
        capabilities.add("structured_output")
    if "reasoning" in supported_parameters:
        capabilities.add("reasoning")
    if any(modality in {"image", "video"} for modality in input_modalities):
        capabilities.add("vision")
    return {
        "id": model_id,
        "canonical_slug": row.get("canonical_slug"),
        "name": model_name,
        "family": str(model_id.split("/", 1)[0] or ""),
        "description": row.get("description"),
        "created": created,
        "created_ts": created_ts,
        "expiration_date": row.get("expiration_date"),
        "context_length": row.get("context_length"),
        "provider_context_length": top_provider.get("context_length"),
        "max_completion_tokens": top_provider.get("max_completion_tokens"),
        "pricing": pricing,
        "supported_parameters": supported_parameters,
        "input_modalities": input_modalities,
        "output_modalities": output_modalities,
        "capabilities": sorted(capabilities),
        "supports_tools": "tools" in supported_parameters,
        "supports_structured_outputs": "structured_outputs" in supported_parameters,
        "supports_json_schema": ("structured_outputs" in supported_parameters) or ("response_format" in supported_parameters),
        "supports_reasoning": "reasoning" in supported_parameters,
        "supports_vision": any(modality in {"image", "video"} for modality in input_modalities),
        "supports_text": not output_modalities or "text" in output_modalities,
        "is_free": is_free,
        "free_detection_source": free_detection_source,
        "popularity_rank": ranking_row.get("popularity_rank") if isinstance(ranking_row, dict) else None,
        "popularity_tokens": ranking_row.get("popularity_tokens") if isinstance(ranking_row, dict) else None,
        "top_weekly_rank": ranking_row.get("top_weekly_rank") if isinstance(ranking_row, dict) else None,
        "category_ranks": ranking_row.get("category_ranks", {}) if isinstance(ranking_row, dict) else {},
        **size_data,
    }


def _merge_openrouter_rankings_into_models(models: list[dict], rankings: list[dict]) -> list[dict]:
    rankings_lookup = _openrouter_rankings_lookup(rankings)
    merged = []
    for row in models if isinstance(models, list) else []:
        if not isinstance(row, dict):
            continue
        updated = dict(row)
        ranking_row = _openrouter_ranking_for_model(str(updated.get("id", "")), str(updated.get("name", "")), rankings_lookup)
        updated["popularity_rank"] = ranking_row.get("popularity_rank") if isinstance(ranking_row, dict) else None
        updated["popularity_tokens"] = ranking_row.get("popularity_tokens") if isinstance(ranking_row, dict) else None
        updated["top_weekly_rank"] = ranking_row.get("top_weekly_rank") if isinstance(ranking_row, dict) else None
        updated["category_ranks"] = ranking_row.get("category_ranks", {}) if isinstance(ranking_row, dict) else {}
        merged.append(updated)
    return merged


def _openrouter_catalog_model_map(catalog: dict) -> dict[str, dict]:
    models = catalog.get("models", []) if isinstance(catalog, dict) else []
    return {
        str(row.get("id", "")): row
        for row in models
        if isinstance(row, dict) and str(row.get("id", ""))
    }


def _manual_openrouter_free_candidates_state() -> dict:
    state = read_provider_runtime_state()
    section = state.get("openrouter_free_candidates", {})
    return section if isinstance(section, dict) else {}


@_state_transactional
def _store_manual_openrouter_free_candidates(payload: dict):
    state = read_provider_runtime_state()
    state["openrouter_free_candidates"] = payload
    write_provider_runtime_state(state)


def _smoke_check_openrouter_candidates(env: dict, payload: dict, req: OpenRouterFreeDiscoveryReq) -> dict:
    candidates = payload.get("candidates", []) if isinstance(payload.get("candidates", []), list) else []
    smoke_top_n = max(0, int(req.smoke_top_n or 0))
    timeout_s = max(5, min(90, int(req.smoke_timeout_s or 15)))
    prompt = str(req.smoke_prompt or "Reply with OK only.").strip() or "Reply with OK only."
    require_tools = bool(req.require_tools is True)
    require_structured = bool(req.require_structured_outputs is True)
    if require_structured:
        prompt = (
            f"{prompt}\nRespond ONLY as JSON matching schema with field 'ok' set to true."
        )
    if require_tools:
        prompt = (
            f"{prompt}\nUse the ping tool exactly once with argument {{\"message\": \"smoke\"}}."
        )
    if smoke_top_n <= 0 or not candidates:
        return {
            "tested_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "passed_ids": [],
            "promoted_ids": [],
            "results": [],
        }

    adapter = OpenRouterProviderAdapter()
    passed_ids = []
    promoted_ids = []
    results = []

    tested = 0
    for candidate in candidates:
        if tested >= smoke_top_n:
            break
        if not isinstance(candidate, dict):
            continue
        model_id = str(candidate.get("id", "") or "").strip()
        if not model_id:
            continue
        pstate = str(candidate.get("promotion_state", "") or "").lower()
        if pstate in {"quarantined", "retired"}:
            continue

        tested += 1
        start = time.time()
        try:
            smoke_payload = {
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 48,
                "temperature": 0,
            }
            if require_structured:
                smoke_payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "smoke_check",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                            },
                            "required": ["ok"],
                            "additionalProperties": False,
                        },
                    },
                }
            if require_tools:
                smoke_payload["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": "ping",
                            "description": "Smoke check tool",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                },
                                "required": ["message"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ]

            raw = adapter.chat({
                "base": env.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
                "api_key": env.get("OPENROUTER_API_KEY", ""),
                "payload": smoke_payload,
                "x_title": "llm-manager-smoke",
                "http_referer": "http://127.0.0.1/llm-manager",
                "timeout": timeout_s,
            })
            text = _extract_chat_text(raw).strip()
            latency_ms = int((time.time() - start) * 1000)

            choices = raw.get("choices", []) if isinstance(raw, dict) and isinstance(raw.get("choices"), list) else []
            first_choice = choices[0] if choices else {}
            message_obj = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
            tool_calls = message_obj.get("tool_calls", []) if isinstance(message_obj, dict) and isinstance(message_obj.get("tool_calls"), list) else []

            structured_ok = True
            if require_structured:
                structured_ok = False
                try:
                    parsed = json.loads(text)
                    structured_ok = isinstance(parsed, dict) and isinstance(parsed.get("ok"), bool)
                except Exception:
                    structured_ok = False

            tools_ok = True
            if require_tools:
                tools_ok = bool(tool_calls)

            if not text:
                normalized = {
                    "type": "smoke_empty_response",
                    "message": "Smoke check returned empty text",
                    "retryable": True,
                }
                _mark_provider_failure("openrouter", model_id, normalized)
                _set_provider_model_promotion_state("openrouter", model_id, "quarantined", reason="smoke_empty_response", actor=req.actor)
                evidence = {
                    "ts": datetime.utcnow().isoformat() + "Z",
                    "status": "failed",
                    "latency_ms": latency_ms,
                    "error_type": str(normalized.get("type", "provider_error")),
                    "error_message": str(normalized.get("message", "") or ""),
                    "prompt_preview": prompt[:120],
                    "timeout_s": timeout_s,
                    "actor": req.actor,
                    "reason": req.reason or None,
                }
                _append_openrouter_smoke_evidence(model_id, evidence)
                results.append({
                    "model": model_id,
                    "status": "failed",
                    "latency_ms": latency_ms,
                    "error": normalized,
                    "evidence": evidence,
                })
                continue

            if not structured_ok or not tools_ok:
                mismatch_parts = []
                if not structured_ok:
                    mismatch_parts.append("structured_output_check_failed")
                if not tools_ok:
                    mismatch_parts.append("tools_check_failed")
                mismatch_message = ",".join(mismatch_parts) or "capability_mismatch"
                normalized = {
                    "type": "smoke_capability_mismatch",
                    "message": mismatch_message,
                    "retryable": False,
                }
                _mark_provider_failure("openrouter", model_id, normalized)
                _set_provider_model_promotion_state("openrouter", model_id, "quarantined", reason="smoke_capability_mismatch", actor=req.actor)
                evidence = {
                    "ts": datetime.utcnow().isoformat() + "Z",
                    "status": "failed",
                    "latency_ms": latency_ms,
                    "error_type": str(normalized.get("type", "provider_error")),
                    "error_message": str(normalized.get("message", "") or ""),
                    "prompt_preview": prompt[:120],
                    "timeout_s": timeout_s,
                    "actor": req.actor,
                    "reason": req.reason or None,
                    "requirements": {
                        "tools": require_tools,
                        "structured_outputs": require_structured,
                    },
                    "checks": {
                        "tools_ok": tools_ok,
                        "structured_ok": structured_ok,
                    },
                }
                _append_openrouter_smoke_evidence(model_id, evidence)
                results.append({
                    "model": model_id,
                    "status": "failed",
                    "latency_ms": latency_ms,
                    "error": normalized,
                    "evidence": evidence,
                })
                continue

            _mark_provider_success("openrouter", model_id)
            _set_provider_model_promotion_state("openrouter", model_id, "smoke_passed", reason="smoke_check_pass", actor=req.actor)
            passed_ids.append(model_id)
            evidence = {
                "ts": datetime.utcnow().isoformat() + "Z",
                "status": "passed",
                "latency_ms": latency_ms,
                "response_preview": text[:120],
                "usage": raw.get("usage", {}) if isinstance(raw, dict) else {},
                "prompt_preview": prompt[:120],
                "timeout_s": timeout_s,
                "actor": req.actor,
                "reason": req.reason or None,
                "requirements": {
                    "tools": require_tools,
                    "structured_outputs": require_structured,
                },
            }
            _append_openrouter_smoke_evidence(model_id, evidence)
            results.append({
                "model": model_id,
                "status": "passed",
                "latency_ms": latency_ms,
                "response_preview": text[:120],
                "usage": raw.get("usage", {}) if isinstance(raw, dict) else {},
                "evidence": evidence,
            })
        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            normalized = _normalize_provider_error("openrouter", e)
            _mark_provider_failure("openrouter", model_id, normalized)
            if str(normalized.get("type", "") or "") in {"not_free_anymore", "model_unavailable", "auth_error"}:
                _set_provider_model_promotion_state("openrouter", model_id, "retired", reason=str(normalized.get("type", "")), actor=req.actor)
            evidence = {
                "ts": datetime.utcnow().isoformat() + "Z",
                "status": "failed",
                "latency_ms": latency_ms,
                "error_type": str(normalized.get("type", "provider_error")),
                "error_message": str(normalized.get("message", "") or ""),
                "retryable": bool(normalized.get("retryable", False)),
                "prompt_preview": prompt[:120],
                "timeout_s": timeout_s,
                "actor": req.actor,
                "reason": req.reason or None,
            }
            _append_openrouter_smoke_evidence(model_id, evidence)
            results.append({
                "model": model_id,
                "status": "failed",
                "latency_ms": latency_ms,
                "error": normalized,
                "evidence": evidence,
            })

    promote_limit = int(req.activate_top_n or 0)
    if promote_limit <= 0:
        promote_limit = max(0, int(req.auto_promote_top_n or 0))
    if promote_limit > 0:
        promoted_ids = passed_ids[:promote_limit]
        for model_id in promoted_ids:
            _set_provider_model_promotion_state("openrouter", model_id, "active", reason="auto_smoke_promote", actor=req.actor)

    return {
        "tested_count": tested,
        "passed_count": len(passed_ids),
        "failed_count": max(0, tested - len(passed_ids)),
        "passed_ids": passed_ids,
        "promoted_ids": promoted_ids,
        "results": results,
    }


@_state_transactional
def _commit_openrouter_rankings(snapshot: dict) -> dict:
    state = read_provider_runtime_state()
    cache = state.get("openrouter_catalog_cache", {}) if isinstance(state.get("openrouter_catalog_cache"), dict) else {}
    rankings = snapshot.get("rankings", []) if isinstance(snapshot.get("rankings"), list) else []
    cache["rankings"] = rankings
    cache["rankings_fetched_ts"] = snapshot.get("fetched_ts")
    cache["rankings_error"] = snapshot.get("error")
    cache["models"] = _merge_openrouter_rankings_into_models(cache.get("models", []), rankings)
    state["openrouter_catalog_cache"] = cache
    write_provider_runtime_state(state)
    return cache


def _refresh_openrouter_rankings_in_cache() -> dict:
    return _commit_openrouter_rankings(_fetch_openrouter_rankings())


def _score_openrouter_candidate(row: dict) -> float:
    score = 0.0
    popularity_rank = row.get("popularity_rank")
    popularity_tokens = row.get("popularity_tokens")
    top_weekly_rank = row.get("top_weekly_rank")
    category_ranks = row.get("category_ranks", {}) if isinstance(row.get("category_ranks"), dict) else {}
    context_length = float(row.get("context_length") or 0)
    if popularity_rank is not None:
        score += max(0.0, 120.0 - (float(popularity_rank) * 8.0))
    if popularity_tokens is not None:
        score += min(40.0, float(popularity_tokens) / 250_000_000_000.0)
    if top_weekly_rank is not None:
        score += max(0.0, 80.0 - (float(top_weekly_rank) * 5.0))
    if category_ranks:
        numeric_ranks = []
        for value in category_ranks.values():
            try:
                numeric_ranks.append(float(value))
            except Exception:
                continue
        if numeric_ranks:
            score += max(0.0, 40.0 - (min(numeric_ranks) * 2.0))
        score += min(12.0, float(len(category_ranks)) * 2.0)
    score += min(20.0, context_length / 65536.0)
    if row.get("supports_tools"):
        score += 12.0
    if row.get("supports_structured_outputs"):
        score += 8.0
    if row.get("supports_reasoning"):
        score += 5.0
    failure_24h = float(row.get("failure_count_24h", 0) or 0)
    failure_7d = float(row.get("failure_count_7d", 0) or 0)
    health_status = str(row.get("health_status", "unknown"))
    if health_status == "healthy":
        score += 10.0
    elif health_status in {"manual_review", "quarantined"}:
        score -= 100.0
    elif health_status == "retired":
        score -= 200.0
    elif health_status == "cooldown":
        score -= 25.0
    else:
        score -= float(row.get("failure_count", 0) or 0) * 5.0
    score -= failure_24h * 6.0
    score -= failure_7d * 1.5
    return round(score, 3)


def _build_openrouter_free_candidates(provider_models: dict, discovery_req: OpenRouterFreeDiscoveryReq) -> dict:
    state = read_provider_runtime_state()
    catalog = state.get("openrouter_catalog_cache", {}) if isinstance(state.get("openrouter_catalog_cache"), dict) else {}
    models = catalog.get("models", []) if isinstance(catalog.get("models"), list) else []
    if not models:
        raise HTTPException(400, "OpenRouter catalog cache is empty. Refresh or discover with refresh_catalog=true first.")

    curated_ids = {
        str(entry.get("id", ""))
        for entry in provider_models.get("openrouter", {}).get("free", [])
        if isinstance(entry, dict) and str(entry.get("id", ""))
    }
    family_allow = {_normalize_openrouter_author(v) for v in discovery_req.family_allow if str(v).strip()}
    family_deny = {_normalize_openrouter_author(v) for v in discovery_req.family_deny if str(v).strip()}
    existing = state.get("openrouter_free_candidates", {}) if isinstance(state.get("openrouter_free_candidates"), dict) else {}
    existing_active_ids = {
        str(mid)
        for mid in (existing.get("active_ids", []) if isinstance(existing.get("active_ids", []), list) else [])
        if str(mid)
    }
    candidates = []
    skipped = {
        "not_free": 0,
        "curated": 0,
        "family": 0,
        "context": 0,
        "size": 0,
        "popularity": 0,
        "capabilities": 0,
    }

    for row in models:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("is_free", False)):
            skipped["not_free"] += 1
            continue
        model_id = str(row.get("id", ""))
        capabilities = {
            str(capability).strip().lower()
            for capability in (row.get("capabilities", []) if isinstance(row.get("capabilities"), list) else [])
            if str(capability).strip()
        }
        if "chat" not in capabilities:
            skipped["capabilities"] += 1
            continue
        if not discovery_req.include_curated and model_id in curated_ids:
            skipped["curated"] += 1
            continue
        family_key = _normalize_openrouter_author(row.get("family", ""))
        if family_allow and family_key not in family_allow:
            skipped["family"] += 1
            continue
        if family_deny and family_key in family_deny:
            skipped["family"] += 1
            continue
        context_length = int(row.get("context_length", 0) or 0)
        if discovery_req.min_context_length is not None and context_length < int(discovery_req.min_context_length):
            skipped["context"] += 1
            continue
        total_params_b = row.get("estimated_total_params_b")
        active_params_b = row.get("estimated_active_params_b")
        if not discovery_req.allow_unknown_size and total_params_b is None and active_params_b is None:
            skipped["size"] += 1
            continue
        if discovery_req.max_total_params_b is not None and total_params_b is not None and float(total_params_b) > float(discovery_req.max_total_params_b):
            skipped["size"] += 1
            continue
        if discovery_req.max_active_params_b is not None and active_params_b is not None and float(active_params_b) > float(discovery_req.max_active_params_b):
            skipped["size"] += 1
            continue
        popularity_tokens = row.get("popularity_tokens")
        if discovery_req.min_popularity_tokens is not None:
            if popularity_tokens is None or float(popularity_tokens) < float(discovery_req.min_popularity_tokens):
                skipped["popularity"] += 1
                continue
        if discovery_req.require_tools is True and not bool(row.get("supports_tools", False)):
            skipped["capabilities"] += 1
            continue
        if discovery_req.require_structured_outputs is True and not bool(row.get("supports_structured_outputs", row.get("supports_json_schema", False))):
            skipped["capabilities"] += 1
            continue
        if discovery_req.require_reasoning is True and not bool(row.get("supports_reasoning", False)):
            skipped["capabilities"] += 1
            continue
        if discovery_req.require_vision is True and not bool(row.get("supports_vision", False)):
            skipped["capabilities"] += 1
            continue

        model_state = _provider_model_state_row("openrouter", model_id)
        counts = _refresh_failure_window_fields(model_state)
        promotion_state = str(model_state.get("promotion_state", "") or "discovered").lower()
        if promotion_state not in PROMOTION_STATES:
            promotion_state = "discovered"
        if _openrouter_expiration_is_past(row.get("expiration_date")):
            promotion_state = "retired"
        elif model_id in existing_active_ids:
            promotion_state = "active"
        elif promotion_state == "discovered":
            promotion_state = "candidate"
        if bool(model_state.get("disabled_until_manual_review", False)) or bool(model_state.get("exclude_from_free_rotation", False)):
            if promotion_state != "retired":
                promotion_state = "quarantined"

        candidate = dict(row)
        candidate["failure_count"] = int(model_state.get("failure_count", 0) or 0)
        candidate["failure_count_24h"] = int(counts.get("failure_count_24h", 0) or 0)
        candidate["failure_count_7d"] = int(counts.get("failure_count_7d", 0) or 0)
        candidate["last_success_ts"] = model_state.get("last_success_ts")
        candidate["last_failure_ts"] = model_state.get("last_failure_ts")
        candidate["last_error_type"] = model_state.get("last_error_type")
        candidate["last_error_message"] = model_state.get("last_error_message")
        candidate["last_error_status_code"] = model_state.get("last_error_status_code")
        candidate["last_error_provider_code"] = model_state.get("last_error_provider_code")
        candidate["last_error_provider_type"] = model_state.get("last_error_provider_type")
        candidate["promotion_state"] = promotion_state
        candidate["health_status"] = _openrouter_health_status(model_state)
        candidate["activation_eligible"] = (
            not bool(model_state.get("disabled_until_manual_review", False))
            and not bool(model_state.get("exclude_from_free_rotation", False))
            and promotion_state not in {"quarantined", "retired"}
        )
        candidate["score"] = _score_openrouter_candidate(candidate)
        candidates.append(candidate)

    sort_by = str(discovery_req.sort_by or "score")
    if sort_by == "popularity":
        candidates.sort(
            key=lambda row: (
                (row.get("popularity_rank") is None),
                row.get("popularity_rank") or 999999,
                row.get("top_weekly_rank") or 999999,
                -(float(row.get("context_length", 0) or 0)),
            )
        )
    elif sort_by == "context":
        candidates.sort(key=lambda row: (-(float(row.get("context_length", 0) or 0)), (row.get("popularity_rank") or 999999)))
    elif sort_by == "newest":
        candidates.sort(key=lambda row: (-(float(row.get("created") or 0)), (row.get("popularity_rank") or 999999)))
    else:
        candidates.sort(key=lambda row: (-(float(row.get("score", 0) or 0)), (row.get("popularity_rank") or 999999), -(float(row.get("context_length", 0) or 0))))

    max_candidates = max(1, int(discovery_req.max_candidates or 20))
    limited_candidates = candidates[:max_candidates]
    if discovery_req.clear_active_ids:
        active_ids = []
    elif int(discovery_req.activate_top_n or 0) > 0:
        limit = max(0, int(discovery_req.activate_top_n or 0))
        active_ids = [
            str(row.get("id", ""))
            for row in limited_candidates
            if bool(row.get("activation_eligible", False))
        ][:limit]
    else:
        active_ids = [str(mid) for mid in existing.get("active_ids", []) if str(mid)]

    return {
        "updated_ts": datetime.utcnow().isoformat() + "Z",
        "source_catalog_fetched_ts": catalog.get("fetched_ts"),
        "filters": discovery_req.model_dump(),
        "candidate_count": len(limited_candidates),
        "candidates": limited_candidates,
        "active_ids": active_ids,
        "activation_mode": "manual",
        "last_actor": discovery_req.actor,
        "last_reason": discovery_req.reason,
        "skipped_counts": skipped,
    }


@_state_transactional
def _commit_openrouter_catalog(fetched: dict) -> dict:
    state = read_provider_runtime_state()
    models = fetched.get("models", []) if isinstance(fetched.get("models"), list) else []
    model_state = state.get("provider_model_state", {}) if isinstance(state.get("provider_model_state"), dict) else {}
    free_id_set = set(fetched.get("free_ids", []))

    if not fetched.get("error"):
        for model_row in models:
            if not isinstance(model_row, dict):
                continue
            model_id = str(model_row.get("id", "") or "")
            if not model_id:
                continue
            key = _provider_model_key("openrouter", model_id)
            row_state = model_state.get(key, {}) if isinstance(model_state.get(key), dict) else {}
            if _openrouter_expiration_is_past(model_row.get("expiration_date")):
                row_state["exclude_from_free_rotation"] = True
                _set_promotion_state_on_row(row_state, "retired", reason="expired")
            elif bool(model_row.get("is_free", False)):
                if str(row_state.get("promotion_state", "") or "") not in PROMOTION_STATES:
                    _set_promotion_state_on_row(row_state, "discovered", reason="catalog_refresh")
            _refresh_failure_window_fields(row_state)
            model_state[key] = row_state

        for key, row_state in list(model_state.items()):
            if not isinstance(row_state, dict) or not str(key).startswith("openrouter:"):
                continue
            model_id = str(key).split(":", 1)[1]
            promotion_state = str(row_state.get("promotion_state", "") or "").lower()
            if model_id and model_id not in free_id_set and promotion_state in {"discovered", "candidate", "smoke_passed", "active"}:
                row_state["exclude_from_free_rotation"] = True
                _set_promotion_state_on_row(row_state, "retired", reason="upstream_not_free")
                model_state[key] = row_state

    state["provider_model_state"] = model_state
    state["openrouter_catalog_cache"] = fetched
    write_provider_runtime_state(state)
    return fetched


def _refresh_openrouter_catalog(env: dict, state: dict | None = None, include_rankings: bool = False) -> dict:
    base = str(env.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")).rstrip("/")
    api_key = str(env.get("OPENROUTER_API_KEY", "") or "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    state = state if isinstance(state, dict) else read_provider_runtime_state()
    prior_cache = state.get("openrouter_catalog_cache", {}) if isinstance(state.get("openrouter_catalog_cache"), dict) else {}
    ranking_rows = prior_cache.get("rankings", []) if isinstance(prior_cache.get("rankings"), list) else []
    rankings_fetched_ts = prior_cache.get("rankings_fetched_ts")
    rankings_error = prior_cache.get("rankings_error")
    if include_rankings:
        ranking_snapshot = _fetch_openrouter_rankings()
        ranking_rows = ranking_snapshot.get("rankings", []) if isinstance(ranking_snapshot.get("rankings"), list) else []
        rankings_fetched_ts = ranking_snapshot.get("fetched_ts")
        rankings_error = ranking_snapshot.get("error")
    rankings_lookup = _openrouter_rankings_lookup(ranking_rows)

    fetched = {
        "fetched_ts": datetime.utcnow().isoformat() + "Z",
        "count": 0,
        "free_ids": [],
        "models": [],
        "rankings": ranking_rows,
        "rankings_fetched_ts": rankings_fetched_ts,
        "rankings_error": rankings_error,
        "error": None,
    }
    try:
        r = requests.get(
            f"{base}/models",
            params={"output_modalities": "all"},
            headers=headers,
            timeout=20,
        )
        r.raise_for_status()
        raw = r.json()
        rows = raw.get("data", []) if isinstance(raw, dict) else []
        models = []
        free_ids = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id", "") or "").strip()
            if not mid:
                continue
            pricing = row.get("pricing", {}) if isinstance(row.get("pricing"), dict) else {}
            is_free, _ = _detect_openrouter_free(pricing, mid, str(row.get("name", "") or mid))
            if is_free:
                free_ids.append(mid)
            enriched = _build_openrouter_catalog_model(row, rankings_lookup)
            if enriched is not None:
                models.append(enriched)
        fetched["models"] = models
        fetched["count"] = len(models)
        fetched["free_ids"] = sorted(set(free_ids))

    except Exception as e:
        fetched["error"] = str(e)

    return _commit_openrouter_catalog(fetched)


def _maybe_refresh_openrouter_catalog(env: dict, max_age_seconds: int = 900) -> dict:
    state = read_provider_runtime_state()
    cache = state.get("openrouter_catalog_cache", {}) if isinstance(state.get("openrouter_catalog_cache"), dict) else {}
    fetched_ts = str(cache.get("fetched_ts", "") or "")
    dt = _parse_iso_ts(fetched_ts)
    now_dt = datetime.utcnow().astimezone()
    if dt is not None:
        age = (now_dt - dt).total_seconds()
        if age >= 0 and age < max_age_seconds:
            return cache
    return _refresh_openrouter_catalog(env, state=state)


def _eval_base_for_mode(mode: str, env: dict, provider_models: dict | None = None) -> str:
    mode_key = _normalize_slot_mode(mode)
    backend = read_slot_backends().get(mode_key, DEFAULT_SLOT_BACKENDS.get(mode_key, "tgw"))
    base, _ = _local_base_for_mode(mode_key, env, backend=backend, provider_models=provider_models)
    return base


def _default_eval_models(mode: str) -> list[str]:
    alias = {"chat": "chat_active_model", "intent": "intent_active_model", "small": "small_active_model"}[mode]
    active = current_links().get(mode)
    active_name = os.path.basename(active.rstrip("/")) if active else None
    out = [alias]
    if active_name and active_name != alias:
        out.append(active_name)
    return out


def _extract_chat_text(raw: dict) -> str:
    if not isinstance(raw, dict):
        return ""
    choices = raw.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    msg = first.get("message", {}) if isinstance(first, dict) else {}
    if isinstance(msg, dict):
        return str(msg.get("content", "") or "")
    return ""


def _catalog_entries_by_provider(provider_models: dict, provider: str) -> list[dict]:
    if provider == "openrouter":
        free = provider_models.get("openrouter", {}).get("free", [])
        paid = provider_models.get("openrouter", {}).get("paid", [])
        return [e for e in (free + paid) if isinstance(e, dict)]
    if provider == "openai":
        allowed = provider_models.get("openai", {}).get("allowed", [])
        return [e for e in allowed if isinstance(e, dict)]
    return []


def _resolve_eval_candidate(raw_candidate: str, target_mode: str, provider_models: dict) -> dict:
    raw = str(raw_candidate or "").strip()
    if not raw:
        raw = {
            "chat": "chat_active_model",
            "intent": "intent_active_model",
            "small": "small_active_model",
        }.get(target_mode, "chat_active_model")

    mode = target_mode
    provider = "local"
    lane = "local"
    model = raw

    if raw.startswith("openrouter.free:"):
        provider = "openrouter"
        lane = "openrouter_free"
        model = raw.split(":", 1)[1].strip()
    elif raw.startswith("openrouter.paid:"):
        provider = "openrouter"
        lane = "openrouter_paid"
        model = raw.split(":", 1)[1].strip()
    elif raw.startswith("openrouter:"):
        provider = "openrouter"
        model = raw.split(":", 1)[1].strip()
        free_ids = {str(e.get("id", "")) for e in _catalog_entries_by_provider(provider_models, "openrouter") if e.get("tier") == "free"}
        paid_ids = {str(e.get("id", "")) for e in _catalog_entries_by_provider(provider_models, "openrouter") if e.get("tier") == "paid"}
        if model in free_ids:
            lane = "openrouter_free"
        elif model in paid_ids:
            lane = "openrouter_paid"
        else:
            lane = "openrouter"
    elif raw.startswith("openai:"):
        provider = "openai"
        lane = "openai"
        model = raw.split(":", 1)[1].strip()
    elif raw.startswith("local:"):
        rem = raw.split(":", 1)[1].strip()
        parts = rem.split(":", 1)
        if len(parts) == 2 and parts[0] in SLOT_MODES:
            mode = parts[0]
            model = parts[1].strip()
        else:
            model = rem

    if not model:
        model = {
            "chat": "chat_active_model",
            "intent": "intent_active_model",
            "small": "small_active_model",
        }.get(mode, "chat_active_model")

    return {
        "raw": raw,
        "provider": provider,
        "lane": lane,
        "model": model,
        "mode": mode,
    }


def _resolve_eval_candidates(req: LocalEvalRequest, provider_models: dict) -> list[dict]:
    candidates = req.candidate_models or _default_eval_models(req.target_mode)
    return [_resolve_eval_candidate(c, req.target_mode, provider_models) for c in candidates]


def _extract_pricing(entry: dict) -> tuple[float | None, float | None]:
    input_keys = ("input_cost_per_1k", "input_cost_usd_per_1k", "prompt_cost_per_1k", "prompt_cost_usd_per_1k")
    output_keys = ("output_cost_per_1k", "output_cost_usd_per_1k", "completion_cost_per_1k", "completion_cost_usd_per_1k")
    in_cost = None
    out_cost = None
    for k in input_keys:
        if k in entry:
            try:
                in_cost = float(entry[k])
            except Exception:
                in_cost = None
            break
    for k in output_keys:
        if k in entry:
            try:
                out_cost = float(entry[k])
            except Exception:
                out_cost = None
            break
    return in_cost, out_cost


def _estimate_eval_cost_usd(provider_models: dict, provider: str, model: str, usage: dict) -> float | None:
    if provider not in ("openrouter", "openai"):
        return 0.0

    entries = _catalog_entries_by_provider(provider_models, provider)
    entry = next((e for e in entries if str(e.get("id", "")) == model), None)
    if not isinstance(entry, dict):
        return None

    input_per_1k, output_per_1k = _extract_pricing(entry)
    if input_per_1k is None and output_per_1k is None:
        return None

    prompt_tokens = float(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = float(usage.get("completion_tokens", 0) or 0)
    total = 0.0
    if input_per_1k is not None:
        total += (prompt_tokens / 1000.0) * input_per_1k
    if output_per_1k is not None:
        total += (completion_tokens / 1000.0) * output_per_1k
    return round(total, 8)


def _run_scoring_plugins(output_text: str, case: dict) -> tuple[list[dict], float, float]:
    plugins = case.get("scoring_plugins", []) if isinstance(case.get("scoring_plugins", []), list) else []
    results = []
    total_score = 0.0
    max_score = 0.0

    for plugin in plugins:
        if not isinstance(plugin, dict):
            continue
        name = str(plugin.get("name", "")).strip()
        if not name:
            continue
        weight = float(plugin.get("weight", 1.0) or 1.0)
        if weight <= 0:
            weight = 1.0

        passed = False
        details = {}

        if name == "regex_match":
            pattern = str(plugin.get("pattern", ""))
            flags = 0
            if "i" in str(plugin.get("flags", "")).lower():
                flags |= re.IGNORECASE
            try:
                passed = bool(re.search(pattern, output_text, flags)) if pattern else False
            except re.error as e:
                passed = False
                details["regex_error"] = str(e)
            details["pattern"] = pattern

        elif name == "contains_any":
            terms = [str(t) for t in plugin.get("terms", []) if str(t)]
            lower_text = output_text.lower()
            matches = [t for t in terms if t.lower() in lower_text]
            passed = len(matches) > 0
            details["terms"] = terms
            details["matches"] = matches

        elif name == "contains_all":
            terms = [str(t) for t in plugin.get("terms", []) if str(t)]
            lower_text = output_text.lower()
            matches = [t for t in terms if t.lower() in lower_text]
            passed = len(matches) == len(terms) and len(terms) > 0
            details["terms"] = terms
            details["matches"] = matches

        elif name == "json_valid":
            required_keys = [str(k) for k in plugin.get("required_keys", []) if str(k)]
            try:
                parsed = json.loads(output_text)
                if required_keys:
                    passed = isinstance(parsed, dict) and all(k in parsed for k in required_keys)
                else:
                    passed = True
                details["required_keys"] = required_keys
            except Exception as e:
                passed = False
                details["json_error"] = str(e)

        elif name == "max_chars":
            max_chars = int(plugin.get("max_chars", 0) or 0)
            passed = max_chars > 0 and len(output_text) <= max_chars
            details["max_chars"] = max_chars
            details["actual_chars"] = len(output_text)

        else:
            details["error"] = f"unknown plugin: {name}"

        score = weight if passed else 0.0
        total_score += score
        max_score += weight
        results.append({
            "name": name,
            "weight": weight,
            "passed": passed,
            "score": score,
            "max_score": weight,
            "details": details,
        })

    return results, total_score, max_score


def _build_eval_summary(run_rows: list[dict]) -> tuple[dict, list[str]]:
    total = len(run_rows)
    success_rows = [r for r in run_rows if r.get("ok")]
    failed_rows = [r for r in run_rows if not r.get("ok")]
    expected_rows = [r for r in run_rows if r.get("expected_contains_total", 0) > 0]
    case_pass_rows = [r for r in run_rows if bool(r.get("case_pass", False))]

    by_model = {}
    by_variant = {}
    by_plugin = {}
    by_provider = {}
    estimated_cost_total = 0.0
    estimated_cost_known_rows = 0
    for row in run_rows:
        model = row.get("model", "unknown")
        variant = row.get("variant_id", "baseline")
        bm = by_model.setdefault(model, {
            "runs": 0,
            "ok": 0,
            "avg_latency_ms": 0.0,
            "avg_output_chars": 0.0,
            "avg_total_tokens": 0.0,
            "expected_contains_hit_rate": None,
            "expected_hits": 0,
            "expected_total": 0,
            "avg_plugin_score": None,
            "avg_plugin_score_pct": None,
            "plugin_score_total": 0.0,
            "plugin_score_max": 0.0,
        })
        bv = by_variant.setdefault(variant, {
            "runs": 0,
            "ok": 0,
            "temperature": row.get("temperature"),
            "avg_latency_ms": 0.0,
            "avg_output_chars": 0.0,
            "avg_total_tokens": 0.0,
            "expected_contains_hit_rate": None,
            "expected_hits": 0,
            "expected_total": 0,
            "avg_plugin_score": None,
            "avg_plugin_score_pct": None,
            "plugin_score_total": 0.0,
            "plugin_score_max": 0.0,
        })
        provider = str(row.get("provider", "unknown"))
        bp = by_provider.setdefault(provider, {
            "runs": 0,
            "ok": 0,
            "avg_latency_ms": 0.0,
            "avg_total_tokens": 0.0,
            "estimated_cost_total_usd": 0.0,
            "estimated_cost_known_rows": 0,
        })
        for bucket in (bm, bv):
            bucket["runs"] += 1
            bucket["ok"] += 1 if row.get("ok") else 0
            bucket["avg_latency_ms"] += float(row.get("latency_ms", 0.0) or 0.0)
            bucket["avg_output_chars"] += int(row.get("output_chars", 0) or 0)
            bucket["avg_total_tokens"] += int(row.get("usage", {}).get("total_tokens", 0) or 0)
            bucket["expected_hits"] += int(row.get("expected_contains_hits", 0) or 0)
            bucket["expected_total"] += int(row.get("expected_contains_total", 0) or 0)
            bucket["plugin_score_total"] += float(row.get("plugin_score", 0.0) or 0.0)
            bucket["plugin_score_max"] += float(row.get("plugin_score_max", 0.0) or 0.0)

        bp["runs"] += 1
        bp["ok"] += 1 if row.get("ok") else 0
        bp["avg_latency_ms"] += float(row.get("latency_ms", 0.0) or 0.0)
        bp["avg_total_tokens"] += int(row.get("usage", {}).get("total_tokens", 0) or 0)

        est_cost = row.get("estimated_cost_usd")
        if est_cost is not None:
            bp["estimated_cost_total_usd"] += float(est_cost)
            bp["estimated_cost_known_rows"] += 1
            estimated_cost_total += float(est_cost)
            estimated_cost_known_rows += 1

        for plugin_result in row.get("plugin_results", []) if isinstance(row.get("plugin_results"), list) else []:
            if not isinstance(plugin_result, dict):
                continue
            pname = str(plugin_result.get("name", "unknown"))
            ps = by_plugin.setdefault(pname, {
                "count": 0,
                "passed": 0,
                "score_total": 0.0,
                "score_max": 0.0,
            })
            ps["count"] += 1
            ps["passed"] += 1 if plugin_result.get("passed") else 0
            ps["score_total"] += float(plugin_result.get("score", 0.0) or 0.0)
            ps["score_max"] += float(plugin_result.get("max_score", 0.0) or 0.0)

    for bucket in (by_model, by_variant):
        for item in bucket.values():
            denom = max(1, int(item.get("runs", 0)))
            item["avg_latency_ms"] = round(item["avg_latency_ms"] / denom, 2)
            item["avg_output_chars"] = round(item["avg_output_chars"] / denom, 2)
            item["avg_total_tokens"] = round(item["avg_total_tokens"] / denom, 2)
            exp_total = int(item.get("expected_total", 0) or 0)
            if exp_total > 0:
                item["expected_contains_hit_rate"] = round(item.get("expected_hits", 0) / exp_total, 4)
            pmax = float(item.get("plugin_score_max", 0.0) or 0.0)
            if pmax > 0:
                item["avg_plugin_score"] = round(float(item.get("plugin_score_total", 0.0)) / denom, 4)
                item["avg_plugin_score_pct"] = round(float(item.get("plugin_score_total", 0.0)) / pmax, 4)

    for p in by_plugin.values():
        count = max(1, int(p.get("count", 0) or 0))
        p["pass_rate"] = round(float(p.get("passed", 0)) / count, 4)
        maxs = float(p.get("score_max", 0.0) or 0.0)
        p["score_pct"] = round(float(p.get("score_total", 0.0)) / maxs, 4) if maxs > 0 else None

    for p in by_provider.values():
        denom = max(1, int(p.get("runs", 0)))
        p["avg_latency_ms"] = round(float(p.get("avg_latency_ms", 0.0)) / denom, 2)
        p["avg_total_tokens"] = round(float(p.get("avg_total_tokens", 0.0)) / denom, 2)
        p["estimated_cost_total_usd"] = round(float(p.get("estimated_cost_total_usd", 0.0)), 8)

    overall_expected_hits = sum(int(r.get("expected_contains_hits", 0) or 0) for r in expected_rows)
    overall_expected_total = sum(int(r.get("expected_contains_total", 0) or 0) for r in expected_rows)

    summary = {
        "total_runs": total,
        "ok_runs": len(success_rows),
        "error_runs": len(failed_rows),
        "success_rate": round((len(success_rows) / total), 4) if total else 0.0,
        "case_pass_count": len(case_pass_rows),
        "case_pass_rate": round((len(case_pass_rows) / total), 4) if total else 0.0,
        "expected_contains_hit_rate": round((overall_expected_hits / overall_expected_total), 4) if overall_expected_total else None,
        "plugin_stats": by_plugin,
        "by_provider": by_provider,
        "estimated_cost_total_usd": round(estimated_cost_total, 8) if estimated_cost_known_rows > 0 else None,
        "estimated_cost_known_rows": estimated_cost_known_rows,
        "by_model": by_model,
        "by_variant": by_variant,
    }

    recommendations = []
    if failed_rows:
        recommendations.append("Some evaluation calls failed; stabilize base endpoint/model availability before comparing prompt settings.")

    if overall_expected_total > 0 and overall_expected_hits / overall_expected_total < 0.7:
        recommendations.append("Expected-content hit rate is low; tighten system prompts and add task-specific instruction templates.")

    plugin_score_max = sum(float(r.get("plugin_score_max", 0.0) or 0.0) for r in run_rows)
    plugin_score_total = sum(float(r.get("plugin_score", 0.0) or 0.0) for r in run_rows)
    if plugin_score_max > 0 and (plugin_score_total / plugin_score_max) < 0.75:
        recommendations.append("Plugin-based scoring indicates weak task-fit; refine prompts and compare targeted variants before model changes.")

    variant_candidates = []
    for variant_id, data in by_variant.items():
        score = data.get("expected_contains_hit_rate")
        if score is None:
            score = data.get("ok", 0) / max(1, data.get("runs", 1))
        variant_candidates.append((float(score), -float(data.get("avg_latency_ms", 0.0)), variant_id))
    if variant_candidates:
        variant_candidates.sort(reverse=True)
        best_variant = variant_candidates[0][2]
        recommendations.append(f"Best observed variant is '{best_variant}'; use it as baseline and test nearby temperatures (+/-0.1).")

    if not recommendations:
        recommendations.append("Evaluation looks stable; expand suites with harder edge cases and add expected_contains checks for regression detection.")

    return summary, recommendations


def _lane_cost_fallback_rank(lane: str, provider: str) -> int:
    lane_l = str(lane or "").lower()
    provider_l = str(provider or "").lower()
    if lane_l == "local" or provider_l == "local":
        return 0
    if lane_l in {"openrouter_free", "openrouter.free", "free"}:
        return 1
    if lane_l in {"openrouter_paid", "openrouter.paid", "openrouter", "paid"}:
        return 2
    if lane_l == "openai" or provider_l == "openai":
        return 3
    return 4


def _task_class_for_run(run: dict) -> tuple[str, str, str]:
    metadata = run.get("metadata", {}) if isinstance(run.get("metadata"), dict) else {}
    project = str(
        metadata.get("project")
        or metadata.get("project_id")
        or metadata.get("caller")
        or "default"
    ).strip() or "default"
    task_class = str(
        metadata.get("task_category")
        or metadata.get("task_type")
        or run.get("target_mode")
        or "unknown"
    ).strip() or "unknown"
    return project, task_class, f"{project}:{task_class}"


def _build_lane_sufficiency_report(
    runs: list[dict],
    min_rows_per_lane: int,
    min_pass_rate: float,
    max_pass_gap: float,
    limit_groups: int,
) -> dict:
    groups: dict[str, dict] = {}

    for run in runs:
        if not isinstance(run, dict):
            continue
        project, task_class, group_key = _task_class_for_run(run)
        row = groups.setdefault(group_key, {
            "task_group": group_key,
            "project": project,
            "task_class": task_class,
            "target_modes": set(),
            "run_ids": set(),
            "latest_created_ts": "",
            "lanes": {},
        })

        run_id = str(run.get("run_id", "") or "")
        if run_id:
            row["run_ids"].add(run_id)
        target_mode = str(run.get("target_mode", "") or "")
        if target_mode:
            row["target_modes"].add(target_mode)
        created_ts = str(run.get("created_ts", "") or "")
        if created_ts > str(row.get("latest_created_ts", "")):
            row["latest_created_ts"] = created_ts

        results = run.get("results", []) if isinstance(run.get("results"), list) else []
        for result in results:
            if not isinstance(result, dict):
                continue
            provider = str(result.get("provider", "unknown") or "unknown")
            lane = str(result.get("lane", provider) or provider)
            lane_key = f"{provider}:{lane}"

            lane_row = row["lanes"].setdefault(lane_key, {
                "provider": provider,
                "lane": lane,
                "rows": 0,
                "pass_rows": 0,
                "latency_sum_ms": 0.0,
                "latency_known_rows": 0,
                "tokens_sum": 0,
                "tokens_known_rows": 0,
                "cost_total_usd": 0.0,
                "cost_known_rows": 0,
                "fallback_rank": _lane_cost_fallback_rank(lane, provider),
            })

            lane_row["rows"] += 1
            passed = result.get("case_pass")
            if passed is None:
                passed = bool(result.get("ok", False))
            lane_row["pass_rows"] += 1 if bool(passed) else 0

            latency_ms = result.get("latency_ms")
            try:
                if latency_ms is not None:
                    lane_row["latency_sum_ms"] += float(latency_ms)
                    lane_row["latency_known_rows"] += 1
            except Exception:
                pass

            usage = result.get("usage", {}) if isinstance(result.get("usage"), dict) else {}
            try:
                total_tokens = int(usage.get("total_tokens", 0) or 0)
                lane_row["tokens_sum"] += total_tokens
                lane_row["tokens_known_rows"] += 1
            except Exception:
                pass

            est_cost = result.get("estimated_cost_usd")
            try:
                if est_cost is not None:
                    lane_row["cost_total_usd"] += float(est_cost)
                    lane_row["cost_known_rows"] += 1
            except Exception:
                pass

    group_rows = []
    for group in groups.values():
        lanes = []
        for lane_bucket in group.get("lanes", {}).values():
            rows_count = int(lane_bucket.get("rows", 0) or 0)
            if rows_count <= 0:
                continue
            pass_rate = float(lane_bucket.get("pass_rows", 0) or 0) / rows_count
            latency_known = int(lane_bucket.get("latency_known_rows", 0) or 0)
            token_known = int(lane_bucket.get("tokens_known_rows", 0) or 0)
            cost_known = int(lane_bucket.get("cost_known_rows", 0) or 0)

            avg_latency = None
            if latency_known > 0:
                avg_latency = round(float(lane_bucket.get("latency_sum_ms", 0.0)) / latency_known, 3)

            avg_tokens = None
            if token_known > 0:
                avg_tokens = round(float(lane_bucket.get("tokens_sum", 0)) / token_known, 3)

            avg_cost = None
            if cost_known > 0:
                avg_cost = round(float(lane_bucket.get("cost_total_usd", 0.0)) / cost_known, 10)

            if avg_cost is not None:
                cost_sort_key = (0, float(avg_cost))
            else:
                cost_sort_key = (1, int(lane_bucket.get("fallback_rank", 9) or 9))

            lanes.append({
                "provider": lane_bucket.get("provider"),
                "lane": lane_bucket.get("lane"),
                "rows": rows_count,
                "pass_rows": int(lane_bucket.get("pass_rows", 0) or 0),
                "pass_rate": round(pass_rate, 4),
                "avg_latency_ms": avg_latency,
                "avg_total_tokens": avg_tokens,
                "avg_estimated_cost_usd": avg_cost,
                "estimated_cost_total_usd": round(float(lane_bucket.get("cost_total_usd", 0.0) or 0.0), 10),
                "cost_known_rows": cost_known,
                "cost_sort_key": cost_sort_key,
            })

        if not lanes:
            continue

        lanes.sort(key=lambda lane_row: (
            -float(lane_row.get("pass_rate", 0.0) or 0.0),
            -int(lane_row.get("rows", 0) or 0),
            float(lane_row.get("avg_latency_ms") if lane_row.get("avg_latency_ms") is not None else 10**12),
            lane_row.get("cost_sort_key", (9, 10**12)),
        ))
        reference_lane = dict(lanes[0])
        reference_pass = float(reference_lane.get("pass_rate", 0.0) or 0.0)

        eligible = [
            lane_row
            for lane_row in lanes
            if int(lane_row.get("rows", 0) or 0) >= int(min_rows_per_lane)
        ]
        sufficient = [
            lane_row
            for lane_row in eligible
            if float(lane_row.get("pass_rate", 0.0) or 0.0) >= float(min_pass_rate)
            and float(lane_row.get("pass_rate", 0.0) or 0.0) >= (reference_pass - float(max_pass_gap))
        ]

        cheapest_sufficient = None
        if sufficient:
            sufficient.sort(key=lambda lane_row: (
                lane_row.get("cost_sort_key", (9, 10**12)),
                -float(lane_row.get("pass_rate", 0.0) or 0.0),
                -int(lane_row.get("rows", 0) or 0),
            ))
            cheapest_sufficient = dict(sufficient[0])

        savings = None
        if cheapest_sufficient is not None:
            ref_cost = reference_lane.get("avg_estimated_cost_usd")
            cheap_cost = cheapest_sufficient.get("avg_estimated_cost_usd")
            if isinstance(ref_cost, (int, float)) and isinstance(cheap_cost, (int, float)) and float(ref_cost) > 0:
                abs_savings = max(0.0, float(ref_cost) - float(cheap_cost))
                savings = {
                    "avg_cost_reduction_usd": round(abs_savings, 10),
                    "avg_cost_reduction_pct": round(abs_savings / float(ref_cost), 6),
                }

        decision = "no_sufficient_lane"
        if cheapest_sufficient is not None:
            if (
                str(cheapest_sufficient.get("lane", "")) == str(reference_lane.get("lane", ""))
                and str(cheapest_sufficient.get("provider", "")) == str(reference_lane.get("provider", ""))
            ):
                decision = "reference_is_cheapest_sufficient"
            else:
                decision = "cheaper_lane_sufficient"

        group_rows.append({
            "task_group": group.get("task_group"),
            "project": group.get("project"),
            "task_class": group.get("task_class"),
            "target_modes": sorted(group.get("target_modes", set())),
            "run_count": len(group.get("run_ids", set())),
            "latest_created_ts": group.get("latest_created_ts"),
            "reference_lane": reference_lane,
            "cheapest_sufficient_lane": cheapest_sufficient,
            "decision": decision,
            "savings": savings,
            "lane_summaries": lanes,
        })

    group_rows.sort(
        key=lambda row: (
            row.get("decision") != "cheaper_lane_sufficient",
            -int(row.get("run_count", 0) or 0),
            str(row.get("latest_created_ts", "")),
        ),
        reverse=False,
    )
    limited_groups = group_rows[: max(1, int(limit_groups))]

    cheaper_count = sum(1 for row in limited_groups if row.get("decision") == "cheaper_lane_sufficient")
    no_sufficient_count = sum(1 for row in limited_groups if row.get("decision") == "no_sufficient_lane")

    recommendations = []
    if cheaper_count > 0:
        recommendations.append(
            "One or more task groups show a cheaper sufficient lane; consider updating defaults or project overrides to reduce cost."
        )
    if no_sufficient_count > 0:
        recommendations.append(
            "Some task groups do not yet have a cheaper sufficient lane under current pass-rate thresholds; tune prompts or widen candidate lanes."
        )
    if not recommendations:
        recommendations.append(
            "Current reference lanes are already cheapest sufficient for sampled groups under active thresholds."
        )

    return {
        "generated_ts": datetime.utcnow().isoformat() + "Z",
        "group_count": len(limited_groups),
        "groups_with_cheaper_sufficient_lane": cheaper_count,
        "groups_without_sufficient_lane": no_sufficient_count,
        "min_rows_per_lane": int(min_rows_per_lane),
        "min_pass_rate": round(float(min_pass_rate), 4),
        "max_pass_gap": round(float(max_pass_gap), 4),
        "groups": limited_groups,
        "recommendations": recommendations,
    }


def _eval_suite_key(suite_name: str, suite_version: str) -> str:
    return f"{suite_name}:{suite_version}"


def _suite_to_request(suite_payload: dict) -> LocalEvalRequest:
    return LocalEvalRequest(
        suite_name=str(suite_payload.get("suite_name", "")),
        suite_version=str(suite_payload.get("suite_version", "1")),
        target_mode=str(suite_payload.get("target_mode", "chat")),
        candidate_models=list(suite_payload.get("candidate_models", [])),
        variants=list(suite_payload.get("variants", [])),
        cases=list(suite_payload.get("cases", [])),
        case_pass_threshold_pct=suite_payload.get("case_pass_threshold_pct"),
        suite_pass_threshold_pct=suite_payload.get("suite_pass_threshold_pct"),
        metadata=dict(suite_payload.get("metadata", {})),
    )


def _priority_rank(priority: str) -> int:
    return int(EVAL_PRIORITY_ORDER.get(priority, 2))


def _parse_iso_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _trim_evaluation_queue(queue_rows: list[dict]) -> list[dict]:
    if len(queue_rows) <= MAX_EVALUATION_QUEUE:
        return queue_rows
    queued = [q for q in queue_rows if isinstance(q, dict) and q.get("status") == "queued"]
    non_queued = [q for q in queue_rows if not isinstance(q, dict) or q.get("status") != "queued"]
    queued.sort(key=lambda x: str(x.get("enqueued_ts", "")), reverse=True)
    keep_queued = queued[: max(0, MAX_EVALUATION_QUEUE - len(non_queued))]
    return non_queued + keep_queued


@_state_transactional
def _upsert_suite_from_request(req: LocalEvalRequest):
    state = read_provider_runtime_state()
    suites = state.get("evaluation_suites", {})
    if not isinstance(suites, dict):
        suites = {}
    key = _eval_suite_key(req.suite_name, req.suite_version)
    suites[key] = {
        "suite_name": req.suite_name,
        "suite_version": req.suite_version,
        "target_mode": req.target_mode,
        "candidate_models": req.candidate_models,
        "variants": [v.model_dump() for v in req.variants],
        "cases": [c.model_dump() for c in req.cases],
        "case_pass_threshold_pct": req.case_pass_threshold_pct,
        "suite_pass_threshold_pct": req.suite_pass_threshold_pct,
        "metadata": redact_sensitive_data(req.metadata),
        "latest_run_id": suites.get(key, {}).get("latest_run_id"),
        "case_count": len(req.cases),
        "variant_count": len(req.variants),
        "updated_ts": datetime.utcnow().isoformat() + "Z",
    }
    state["evaluation_suites"] = suites
    write_provider_runtime_state(state)


@_state_transactional
def _mark_suite_latest_run(suite_name: str, suite_version: str, run_id: str):
    state = read_provider_runtime_state()
    suites = state.get("evaluation_suites", {})
    if not isinstance(suites, dict):
        suites = {}
    key = _eval_suite_key(suite_name, suite_version)
    row = suites.get(key, {}) if isinstance(suites.get(key), dict) else {}
    row["suite_name"] = suite_name
    row["suite_version"] = suite_version
    row["latest_run_id"] = run_id
    row["updated_ts"] = datetime.utcnow().isoformat() + "Z"
    suites[key] = row
    state["evaluation_suites"] = suites
    write_provider_runtime_state(state)


def _build_compare_compact(run: dict) -> dict:
    rows = run.get("results", []) if isinstance(run.get("results"), list) else []
    grouped = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        case_id = str(row.get("case_id", "unknown"))
        variant_id = str(row.get("variant_id", "baseline"))
        key = f"{case_id}::{variant_id}"
        slot = grouped.setdefault(key, {
            "case_id": case_id,
            "variant_id": variant_id,
            "prompt": row.get("prompt"),
            "system": row.get("system"),
            "expected_contains": row.get("expected_contains", []),
            "outputs": [],
        })
        slot["outputs"].append({
            "provider": row.get("provider"),
            "lane": row.get("lane"),
            "model": row.get("model"),
            "ok": row.get("ok"),
            "latency_ms": row.get("latency_ms"),
            "output_text": row.get("output_text"),
            "output_chars": row.get("output_chars"),
            "expected_contains_hits": row.get("expected_contains_hits"),
            "expected_contains_total": row.get("expected_contains_total"),
            "plugin_score": row.get("plugin_score", 0.0),
            "plugin_score_max": row.get("plugin_score_max", 0.0),
            "plugin_results": row.get("plugin_results", []),
            "estimated_cost_usd": row.get("estimated_cost_usd"),
            "usage": row.get("usage", {}),
            "error": row.get("error"),
        })

    return {
        "run_id": run.get("run_id"),
        "suite_name": run.get("suite_name"),
        "suite_version": run.get("suite_version"),
        "target_mode": run.get("target_mode"),
        "created_ts": run.get("created_ts"),
        "summary": run.get("summary", {}),
        "recommendations": run.get("recommendations", []),
        "compare_rows": list(grouped.values()),
    }


@_state_transactional
def _claim_next_eval_job() -> dict | None:
    with EVAL_QUEUE_CLAIM_LOCK:
        state = read_provider_runtime_state()
        queue = state.get("evaluation_queue", []) if isinstance(state.get("evaluation_queue"), list) else []
        runs = state.get("evaluation_runs", {}) if isinstance(state.get("evaluation_runs"), dict) else {}

        # Guardrail: reclaim stale running jobs so priority caps do not block forever.
        now_dt = datetime.utcnow().astimezone()
        stale_changed = False
        for q in queue:
            if not isinstance(q, dict):
                continue
            if q.get("status") != "running":
                continue
            started = _parse_iso_ts(str(q.get("started_ts", "")))
            if started is None:
                continue
            age = (now_dt - started).total_seconds()
            if age > EVAL_RUNNING_STALE_SECONDS:
                run_id = str(q.get("run_id", ""))
                q["status"] = "error"
                q["completed_ts"] = datetime.utcnow().isoformat() + "Z"
                stale_changed = True
                row = runs.get(run_id, {}) if isinstance(runs.get(run_id), dict) else {}
                row["status"] = "error"
                row["error"] = "stale_running_timeout"
                row["completed_ts"] = datetime.utcnow().isoformat() + "Z"
                runs[run_id] = row

        if stale_changed:
            state["evaluation_queue"] = queue
            state["evaluation_runs"] = runs
            write_provider_runtime_state(state)

        running_counts = {"interactive": 0, "batch": 0, "evaluation": 0}
        for q in queue:
            if not isinstance(q, dict):
                continue
            if q.get("status") == "running":
                p = str(q.get("priority", "evaluation"))
                if p not in running_counts:
                    running_counts[p] = 0
                running_counts[p] += 1

        queued = [q for q in queue if isinstance(q, dict) and q.get("status") == "queued"]
        if not queued:
            return None

        queued.sort(key=lambda x: (_priority_rank(str(x.get("priority", "evaluation"))), str(x.get("enqueued_ts", ""))))
        chosen = None
        for q in queued:
            priority = str(q.get("priority", "evaluation"))
            cap = int(EVAL_PRIORITY_RUNNING_CAPS.get(priority, 1))
            if int(running_counts.get(priority, 0)) < cap:
                chosen = q
                break

        if chosen is None:
            return None

        run_id = str(chosen.get("run_id", ""))
        if not run_id:
            return None

        now = datetime.utcnow().isoformat() + "Z"
        for q in queue:
            if isinstance(q, dict) and q.get("run_id") == run_id:
                q["status"] = "running"
                q["started_ts"] = now

        run_row = runs.get(run_id, {}) if isinstance(runs.get(run_id), dict) else {}
        run_row["status"] = "running"
        run_row["started_ts"] = now
        runs[run_id] = run_row

        state["evaluation_queue"] = queue
        state["evaluation_runs"] = runs
        write_provider_runtime_state(state)

        return {
            "run_id": run_id,
            "priority": str(chosen.get("priority", "evaluation")),
            "enqueued_ts": str(chosen.get("enqueued_ts", "")),
            "request": chosen.get("request", {}) if isinstance(chosen.get("request"), dict) else {},
            "started_ts": now,
        }


@_state_transactional
def _finish_eval_job(run_id: str, status: str, error: str | None = None):
    state = read_provider_runtime_state()
    runs = state.get("evaluation_runs", {}) if isinstance(state.get("evaluation_runs"), dict) else {}
    if error is not None:
        row = runs.get(run_id, {}) if isinstance(runs.get(run_id), dict) else {}
        row["status"] = "error"
        row["error"] = error
        row["completed_ts"] = datetime.utcnow().isoformat() + "Z"
        runs[run_id] = row
    queue = state.get("evaluation_queue", []) if isinstance(state.get("evaluation_queue"), list) else []
    for row in queue:
        if isinstance(row, dict) and row.get("run_id") == run_id:
            row["status"] = status
            row["completed_ts"] = datetime.utcnow().isoformat() + "Z"
    state["evaluation_runs"] = runs
    state["evaluation_queue"] = queue
    write_provider_runtime_state(state)


def _eval_worker_once() -> bool:
    job = _claim_next_eval_job()
    if not isinstance(job, dict):
        return False

    run_id = str(job.get("run_id", ""))
    if not run_id:
        return False

    payload = job.get("request", {}) if isinstance(job.get("request"), dict) else {}
    req = _suite_to_request(payload)
    error_message = None
    try:
        _run_local_evaluation(
            req,
            run_id=run_id,
            queued_meta={
                "priority": str(job.get("priority", "evaluation")),
                "enqueued_ts": str(job.get("enqueued_ts", "")),
                "started_ts": str(job.get("started_ts", datetime.utcnow().isoformat() + "Z")),
            },
        )
        final_status = "completed"
    except Exception as e:
        error_message = str(e)
        final_status = "error"

    _finish_eval_job(run_id, final_status, error_message)
    return True


def _eval_worker_loop():
    while True:
        try:
            did_work = _eval_worker_once()
        except Exception:
            did_work = False
        if not did_work:
            time.sleep(EVAL_WORKER_TICK_SECONDS)


def _ensure_eval_worker_started():
    global EVAL_WORKER_STARTED
    with EVAL_WORKER_LOCK:
        if EVAL_WORKER_STARTED:
            return
        for _ in range(EVAL_WORKER_COUNT):
            threading.Thread(target=_eval_worker_loop, daemon=True).start()
        EVAL_WORKER_STARTED = True


@_state_transactional
def _recover_evaluation_jobs() -> dict:
    """Requeue evaluation work whose owning API process disappeared."""
    state = read_provider_runtime_state()
    queue = state.get("evaluation_queue", []) if isinstance(state.get("evaluation_queue"), list) else []
    runs = state.get("evaluation_runs", {}) if isinstance(state.get("evaluation_runs"), dict) else {}
    recovered = 0
    now = datetime.utcnow().isoformat() + "Z"
    queued_ids: set[str] = set()
    for entry in queue:
        if not isinstance(entry, dict):
            continue
        run_id = str(entry.get("run_id", ""))
        if run_id:
            queued_ids.add(run_id)
        if entry.get("status") != "running":
            continue
        entry["status"] = "queued"
        entry.pop("started_ts", None)
        entry["recovered_ts"] = now
        entry["recovery_count"] = int(entry.get("recovery_count", 0) or 0) + 1
        row = runs.get(run_id, {}) if isinstance(runs.get(run_id), dict) else {}
        row["status"] = "queued"
        row.pop("started_ts", None)
        row["recovered_ts"] = now
        row["recovery_count"] = int(row.get("recovery_count", 0) or 0) + 1
        runs[run_id] = row
        recovered += 1

    interrupted = 0
    for run_id, row in runs.items():
        if not isinstance(row, dict) or row.get("status") != "running" or run_id in queued_ids:
            continue
        row["status"] = "error"
        row["error"] = "interrupted_api_restart_missing_queue_entry"
        row["completed_ts"] = now
        interrupted += 1

    if recovered or interrupted:
        state["evaluation_queue"] = queue
        state["evaluation_runs"] = runs
        write_provider_runtime_state(state)
    return {"requeued": recovered, "interrupted": interrupted}


@_state_transactional
def _enqueue_local_evaluation(req: LocalEvalRequest, priority: str) -> tuple[str, int, str]:
    if not req.cases:
        raise HTTPException(400, "cases must contain at least one test case")
    if priority not in EVAL_PRIORITY_ORDER:
        raise HTTPException(400, "priority must be interactive|batch|evaluation")

    _upsert_suite_from_request(req)
    state = read_provider_runtime_state()

    queue = state.get("evaluation_queue", [])
    if not isinstance(queue, list):
        queue = []

    if len([q for q in queue if isinstance(q, dict) and q.get("status") == "queued"]) >= MAX_EVALUATION_QUEUE:
        raise HTTPException(429, "evaluation queue is full")

    run_id = uuid.uuid4().hex[:12]
    enqueued_ts = datetime.utcnow().isoformat() + "Z"
    queue.append({
        "run_id": run_id,
        "priority": priority,
        "status": "queued",
        "enqueued_ts": enqueued_ts,
        "request": req.model_dump(),
    })
    queue = _trim_evaluation_queue(queue)

    runs = state.get("evaluation_runs", {})
    if not isinstance(runs, dict):
        runs = {}
    runs[run_id] = {
        "run_id": run_id,
        "suite_name": req.suite_name,
        "suite_version": req.suite_version,
        "target_mode": req.target_mode,
        "candidate_models": req.candidate_models,
        "variants": [v.model_dump() for v in req.variants],
        "cases": [c.model_dump() for c in req.cases],
        "case_pass_threshold_pct": req.case_pass_threshold_pct,
        "suite_pass_threshold_pct": req.suite_pass_threshold_pct,
        "metadata": redact_sensitive_data(req.metadata),
        "created_ts": enqueued_ts,
        "status": "queued",
        "results": [],
        "summary": {},
        "recommendations": [],
    }

    state["evaluation_queue"] = queue
    state["evaluation_runs"] = runs
    write_provider_runtime_state(state)

    queued = [q for q in queue if isinstance(q, dict) and q.get("status") == "queued"]
    queued.sort(key=lambda x: (_priority_rank(str(x.get("priority", "evaluation"))), str(x.get("enqueued_ts", ""))))
    position = next((i + 1 for i, q in enumerate(queued) if q.get("run_id") == run_id), len(queued))
    _ensure_eval_worker_started()
    return run_id, position, enqueued_ts


@_state_transactional
def _store_local_evaluation(run_record: dict):
    run_id = str(run_record["run_id"])
    suite_key = f"{run_record['suite_name']}:{run_record['suite_version']}"
    state = read_provider_runtime_state()
    runs = state.get("evaluation_runs", {})
    if not isinstance(runs, dict):
        runs = {}
    runs[run_id] = run_record
    if len(runs) > MAX_EVALUATION_RUNS:
        ordered = sorted(runs.items(), key=lambda kv: str(kv[1].get("created_ts", "")))
        for old_id, _ in ordered[:-MAX_EVALUATION_RUNS]:
            runs.pop(old_id, None)

    reports = state.get("evaluation_reports", {})
    if not isinstance(reports, dict):
        reports = {}
    reports[run_id] = {
        "run_id": run_id,
        "suite_name": run_record["suite_name"],
        "suite_version": run_record["suite_version"],
        "created_ts": run_record["created_ts"],
        "suite_pass": run_record["suite_pass"],
        "summary": run_record["summary"],
        "recommendations": run_record["recommendations"],
    }

    suites = state.get("evaluation_suites", {})
    if not isinstance(suites, dict):
        suites = {}
    prior_suite = suites.get(suite_key, {}) if isinstance(suites.get(suite_key), dict) else {}
    suites[suite_key] = {
        "suite_name": run_record["suite_name"],
        "suite_version": run_record["suite_version"],
        "target_mode": run_record["target_mode"],
        "candidate_specs": run_record["candidate_specs"],
        "candidate_models": run_record["candidate_models"],
        "variants": run_record["variants"],
        "cases": run_record["cases"],
        "case_pass_threshold_pct": run_record["case_pass_threshold_pct"],
        "suite_pass_threshold_pct": run_record["suite_pass_threshold_pct"],
        "latest_run_id": run_id,
        "case_count": len(run_record["cases"]),
        "variant_count": len(run_record["variants"]),
        "updated_ts": datetime.utcnow().isoformat() + "Z",
        "metadata": run_record["metadata"],
        "created_ts": prior_suite.get("created_ts", datetime.utcnow().isoformat() + "Z"),
    }
    state["evaluation_runs"] = runs
    state["evaluation_reports"] = reports
    state["evaluation_suites"] = suites
    write_provider_runtime_state(state)


def _run_local_evaluation(req: LocalEvalRequest, run_id: str | None = None, queued_meta: dict | None = None) -> dict:
    if not req.cases:
        raise HTTPException(400, "cases must contain at least one test case")

    _upsert_suite_from_request(req)

    env = read_env()
    provider_models = read_provider_models()
    variants = req.variants or [EvalVariant(variant_id="baseline")]
    candidate_specs = _resolve_eval_candidates(req, provider_models)
    candidate_models = [str(c.get("raw", c.get("model", ""))) for c in candidate_specs]

    run_id = run_id or uuid.uuid4().hex[:12]
    created_ts = datetime.utcnow().isoformat() + "Z"
    started_ts = datetime.utcnow().isoformat() + "Z"
    rows = []

    for candidate in candidate_specs:
        provider = str(candidate.get("provider", "local"))
        lane = str(candidate.get("lane", "local"))
        model = str(candidate.get("model", "chat_active_model"))
        eval_mode = str(candidate.get("mode", req.target_mode))
        local_base = _eval_base_for_mode(eval_mode, env, provider_models=provider_models).rstrip("/")
        for variant in variants:
            for case in req.cases:
                sys_prompt = variant.system if variant.system is not None else case.system
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": case.prompt}],
                    "max_tokens": int(variant.max_tokens or 256),
                }
                if sys_prompt:
                    payload["messages"].insert(0, {"role": "system", "content": sys_prompt})
                if variant.temperature is not None:
                    payload["temperature"] = variant.temperature
                if variant.top_p is not None:
                    payload["top_p"] = variant.top_p
                if variant.stop:
                    payload["stop"] = variant.stop

                start = time.time()
                row = {
                    "run_id": run_id,
                    "case_id": case.case_id,
                    "variant_id": variant.variant_id,
                    "provider": provider,
                    "lane": lane,
                    "candidate_ref": str(candidate.get("raw", model)),
                    "model": model,
                    "target_mode": req.target_mode,
                    "eval_mode": eval_mode,
                    "temperature": variant.temperature,
                    "top_p": variant.top_p,
                    "max_tokens": int(variant.max_tokens or 256),
                    "prompt": case.prompt,
                    "system": sys_prompt,
                    "expected_contains": case.expected_contains,
                    "tags": case.tags,
                    "ts": datetime.utcnow().isoformat() + "Z",
                    "ok": False,
                    "latency_ms": 0,
                    "output_text": "",
                    "output_chars": 0,
                    "expected_contains_hits": 0,
                    "expected_contains_total": len(case.expected_contains),
                    "scoring_plugins": case.scoring_plugins,
                    "min_plugin_score_pct": case.min_plugin_score_pct,
                    "plugin_results": [],
                    "plugin_score": 0.0,
                    "plugin_score_max": 0.0,
                    "plugin_score_pct": None,
                    "case_pass": False,
                    "estimated_cost_usd": None,
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "error": None,
                }
                try:
                    if provider == "local":
                        r = requests.post(f"{local_base}/v1/chat/completions", json=payload, timeout=60)
                        r.raise_for_status()
                        raw = r.json()
                    else:
                        raw = _dispatch_provider_chat(provider, env, payload, provider_models=provider_models)
                    elapsed_ms = round((time.time() - start) * 1000, 2)
                    row["latency_ms"] = elapsed_ms
                    text = _extract_chat_text(raw)
                    lower_text = text.lower()
                    hit_count = 0
                    for token in case.expected_contains:
                        if str(token).lower() in lower_text:
                            hit_count += 1

                    usage_raw = raw.get("usage", {}) if isinstance(raw, dict) else {}
                    row["ok"] = True
                    row["output_text"] = text
                    row["output_chars"] = len(text)
                    row["expected_contains_hits"] = hit_count
                    plugin_results, plugin_score, plugin_score_max = _run_scoring_plugins(text, case.model_dump())
                    row["plugin_results"] = plugin_results
                    row["plugin_score"] = round(plugin_score, 4)
                    row["plugin_score_max"] = round(plugin_score_max, 4)
                    if plugin_score_max > 0:
                        row["plugin_score_pct"] = round(plugin_score / plugin_score_max, 4)

                    case_threshold = case.min_plugin_score_pct
                    req_threshold = req.case_pass_threshold_pct
                    threshold = None
                    if case_threshold is not None:
                        threshold = float(case_threshold)
                    elif req_threshold is not None:
                        threshold = float(req_threshold)

                    pass_by_plugin = True
                    if threshold is not None:
                        pct = row["plugin_score_pct"]
                        pass_by_plugin = pct is not None and float(pct) >= threshold

                    row["case_pass"] = bool(row["ok"]) and pass_by_plugin
                    row["usage"] = {
                        "prompt_tokens": int(usage_raw.get("prompt_tokens", 0) or 0),
                        "completion_tokens": int(usage_raw.get("completion_tokens", 0) or 0),
                        "total_tokens": int(usage_raw.get("total_tokens", 0) or 0),
                    }
                    row["estimated_cost_usd"] = _estimate_eval_cost_usd(provider_models, provider, model, row["usage"])
                except Exception as e:
                    row["latency_ms"] = round((time.time() - start) * 1000, 2)
                    row["error"] = str(e)
                    row["case_pass"] = False

                rows.append(row)

    summary, recommendations = _build_eval_summary(rows)
    suite_pass = all(bool(r.get("case_pass", False)) for r in rows) if rows else False
    if req.suite_pass_threshold_pct is not None:
        suite_pass = float(summary.get("case_pass_rate", 0.0) or 0.0) >= float(req.suite_pass_threshold_pct)
    if not suite_pass:
        recommendations.append("Suite pass criteria not met; adjust prompts/temperatures or revise model selection before promoting this configuration.")

    run_record = {
        "run_id": run_id,
        "suite_name": req.suite_name,
        "suite_version": req.suite_version,
        "target_mode": req.target_mode,
        "candidate_specs": candidate_specs,
        "candidate_models": candidate_models,
        "variants": [v.model_dump() for v in variants],
        "cases": [c.model_dump() for c in req.cases],
        "case_pass_threshold_pct": req.case_pass_threshold_pct,
        "suite_pass_threshold_pct": req.suite_pass_threshold_pct,
        "suite_pass": suite_pass,
        "metadata": redact_sensitive_data(req.metadata),
        "created_ts": created_ts,
        "started_ts": started_ts,
        "completed_ts": datetime.utcnow().isoformat() + "Z",
        "status": "completed",
        "results": rows,
        "summary": summary,
        "recommendations": recommendations,
    }
    if isinstance(queued_meta, dict):
        run_record["queue"] = queued_meta

    _store_local_evaluation(run_record)

    _mark_suite_latest_run(req.suite_name, req.suite_version, run_id)

    return run_record


def current_links():
    def tgt(p: Path):
        try:
            return str(p.resolve())
        except Exception:
            return None
    ck = MODELS_DIR / "chat_active_model"
    ik = MODELS_DIR / "intent_active_model"
    sk = MODELS_DIR / "small_active_model"
    ek = MODELS_DIR / "embed_active_model"
    return {
        "chat":   tgt(ck) if ck.exists() else None,
        "intent": tgt(ik) if ik.exists() else None,
        "small":  tgt(sk) if sk.exists() else None,
        "embed":  tgt(ek) if ek.exists() else None,
    }

# -----------------------------------------------------------------------------
# Helpers: systemd + pm2
# -----------------------------------------------------------------------------
def _pm2(cmd: list[str]):
    try:
        result = subprocess.run(["pm2"] + cmd, capture_output=True, text=True, check=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"pm2 error: {e}")
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"pm2 {' '.join(cmd)} failed: {(result.stderr or result.stdout)[-500:]}")
    return result

def _systemctl(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    # Using sudo because your units are managed at system scope
    return subprocess.run(["sudo", "/bin/systemctl"] + args, capture_output=True, text=True, timeout=timeout)

def _systemctl_restart(unit: str):
    r = _systemctl(["restart", unit])
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=f"systemctl restart failed for {unit}: {(r.stderr or r.stdout)[-500:]}")

def _systemctl_start(unit: str):
    r = _systemctl(["start", unit])
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=f"systemctl start failed for {unit}: {(r.stderr or r.stdout)[-500:]}")

def _systemctl_stop(unit: str):
    r = _systemctl(["stop", unit])
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=f"systemctl stop failed for {unit}: {(r.stderr or r.stdout)[-500:]}")

def _systemctl_show(unit: str) -> dict:
    r = _systemctl(["show", unit, "--no-pager", "--property=Id,ActiveState,SubState,MainPID,ExecStart"])
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout).strip()}
    out = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out

def _journal_tail(unit: str, lines: int = 160) -> str:
    try:
        r = subprocess.run(
            ["sudo", "/bin/journalctl", "-u", unit, "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=30
        )
        return r.stdout or r.stderr or ""
    except Exception as e:
        return f"[journalctl error] {e}"

def _is_listening(port: int) -> bool:
    try:
        r = subprocess.run(["bash", "-lc", f"ss -ltnp | grep -q ':{port} ' && echo yes || echo no"], capture_output=True, text=True)
        return (r.stdout or "").strip() == "yes"
    except Exception:
        return False

def _bounce_engine(mode: str):
    # Prefer systemd units when provided; fallback to pm2.
    a = os.getenv("SYSTEMD_LLM_A")
    b = os.getenv("SYSTEMD_LLM_B")
    c = os.getenv("SYSTEMD_LLM_C")
    d = os.getenv("SYSTEMD_LLM_D")
    unit = {"chat": a, "intent": b, "small": c, "embed": d}.get(mode)

    if unit:
        _systemctl_restart(unit)
        return {"ok": True, "method": "systemd", "unit": unit}

    # Legacy fallback
    env = read_env()
    pm2name = {
        "chat": env.get("PM2_CHAT"),
        "intent": env.get("PM2_INTENT"),
        "small": env.get("PM2_SMALL"),
        "embed": env.get("PM2_EMBED"),
    }.get(mode)
    if pm2name:
        _pm2(["restart", pm2name])
        return {"ok": True, "method": "pm2", "proc": pm2name}

    raise HTTPException(409, f"no engine process is configured for {mode}")


def _stop_engine(mode: str):
    unit = {
        "chat": os.getenv("SYSTEMD_LLM_A"),
        "intent": os.getenv("SYSTEMD_LLM_B"),
        "small": os.getenv("SYSTEMD_LLM_C"),
        "embed": os.getenv("SYSTEMD_LLM_D"),
    }.get(mode)
    if unit:
        _systemctl_stop(unit)
        return {"ok": True, "method": "systemd_stop", "unit": unit}

    env = read_env()
    pm2name = {
        "chat": env.get("PM2_CHAT"),
        "intent": env.get("PM2_INTENT"),
        "small": env.get("PM2_SMALL"),
        "embed": env.get("PM2_EMBED"),
    }.get(mode)
    if pm2name:
        _pm2(["stop", pm2name])
        return {"ok": True, "method": "pm2_stop", "proc": pm2name}
    raise HTTPException(409, f"no engine process is configured for {mode}")


def _normalize_switch_lifecycle_mode(value: str | None) -> str:
    mode = str(value or "legacy").strip().lower() or "legacy"
    if mode not in SWITCH_LIFECYCLE_MODES:
        raise HTTPException(400, f"lifecycle_mode must be one of: {', '.join(sorted(SWITCH_LIFECYCLE_MODES))}")
    return mode


def _backend_supports_model_kind(backend: str, model_kind: str) -> bool:
    backend_key = str(backend or "").strip().lower()
    kind = str(model_kind or "unknown").strip().lower()

    if backend_key == "tgw":
        return kind != "multimodal"
    if backend_key == "tabbyapi":
        if kind == "multimodal":
            return False
        return kind in {"exl2", "exl3", "awq", "gptq"}
    if backend_key == "vllm":
        if kind == "multimodal":
            return False
        return kind in {"transformers", "awq", "gptq", "lora"}
    return False


def _vllm_backend_available() -> bool:
    env = read_env()
    configured = str(env.get("VLLM_PYTHON_BIN", "") or os.getenv("VLLM_PYTHON_BIN", "")).strip()
    if configured:
        return Path(configured).exists()
    return importlib.util.find_spec("vllm") is not None


def _tabbyapi_native_base_for_mode(mode: str) -> tuple[str, str]:
    env = read_env()
    provider_models = read_provider_models()
    base, base_source = _local_base_for_mode(mode, env, backend="tabbyapi", provider_models=provider_models)
    return str(base).rstrip("/"), base_source


def _tabbyapi_native_max_seq_len_for_mode(mode: str, requested: int | None = None) -> int:
    if requested is not None:
        return int(requested)

    mode_key = _normalize_slot_mode(mode)
    env = read_env()
    for key in (
        f"LLM_{mode_key.upper()}_NATIVE_MAX_SEQ_LEN",
        f"LLM_{mode_key.upper()}_MAX_SEQ_LEN",
        "TABBYAPI_NATIVE_MAX_SEQ_LEN",
    ):
        raw = str(env.get(key, "") or "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
            if value > 0:
                return value
        except Exception:
            continue

    return DEFAULT_TABBYAPI_NATIVE_MAX_SEQ_LEN


def _tabbyapi_native_load(mode: str, model_path: Path, max_seq_len: int | None = None) -> dict:
    mode_key = _normalize_slot_mode(mode)
    base, base_source = _tabbyapi_native_base_for_mode(mode_key)
    effective_max_seq_len = _tabbyapi_native_max_seq_len_for_mode(mode_key, max_seq_len)

    path_candidates = ["/v1/model/load", "/model/load"]
    model_name = model_path.name
    detected_kind = detect_kind(model_path)
    preferred_backend = ""
    if detected_kind == "exl3":
        preferred_backend = "exllamav3"
    elif detected_kind in {"exl2", "awq", "gptq"}:
        preferred_backend = "exllamav2"

    base_payload: dict[str, object] = {"model_name": model_name}
    if effective_max_seq_len > 0:
        base_payload["max_seq_len"] = int(effective_max_seq_len)

    payloads: list[dict[str, object]] = []
    if preferred_backend:
        payloads.append({**base_payload, "backend": preferred_backend})
    payloads.append(base_payload)
    # Fallback for installs that expect full model paths for model_name.
    payloads.append({**base_payload, "model_name": str(model_path)})

    errors: list[dict] = []
    for path in path_candidates:
        url = f"{base}{path}"
        for payload in payloads:
            try:
                response = requests.post(url, json=payload, timeout=45)
            except Exception as exc:
                errors.append({"url": url, "payload": payload, "error": str(exc)})
                continue

            parsed_body: object
            try:
                parsed_body = response.json()
            except Exception:
                parsed_body = (response.text or "")[:500]

            if response.ok:
                return {
                    "ok": True,
                    "method": "tabbyapi_native_load",
                    "mode": mode_key,
                    "base": base,
                    "base_source": base_source,
                    "model_kind": detected_kind,
                    "preferred_backend": preferred_backend or None,
                    "effective_max_seq_len": effective_max_seq_len,
                    "url": url,
                    "status_code": int(response.status_code),
                    "payload": payload,
                    "response": parsed_body,
                }

            errors.append(
                {
                    "url": url,
                    "payload": payload,
                    "status_code": int(response.status_code),
                    "response": parsed_body,
                }
            )

    detail = "tabbyapi native load endpoint unavailable"
    if errors:
        last = errors[-1]
        status_code = last.get("status_code")
        if isinstance(status_code, int):
            detail = f"tabbyapi native load failed with status {status_code}"
        elif last.get("error"):
            detail = f"tabbyapi native load request error: {last.get('error')}"

    return {
        "ok": False,
        "method": "tabbyapi_native_load",
        "mode": mode_key,
        "base": base,
        "base_source": base_source,
        "model_kind": detected_kind,
        "preferred_backend": preferred_backend or None,
        "effective_max_seq_len": effective_max_seq_len,
        "detail": detail,
        "attempts": len(errors),
        "errors": errors[-3:],
    }


def _tabbyapi_native_unload(mode: str) -> dict:
    mode_key = _normalize_slot_mode(mode)
    base, base_source = _tabbyapi_native_base_for_mode(mode_key)

    path_candidates = ["/v1/model/unload", "/model/unload"]
    payloads = [{}, {"force": True}]

    errors: list[dict] = []
    for path in path_candidates:
        url = f"{base}{path}"
        for payload in payloads:
            try:
                response = requests.post(url, json=payload, timeout=30)
            except Exception as exc:
                errors.append({"url": url, "payload": payload, "error": str(exc)})
                continue

            parsed_body: object
            try:
                parsed_body = response.json()
            except Exception:
                parsed_body = (response.text or "")[:500]

            if response.ok:
                return {
                    "ok": True,
                    "method": "tabbyapi_native_unload",
                    "mode": mode_key,
                    "base": base,
                    "base_source": base_source,
                    "url": url,
                    "status_code": int(response.status_code),
                    "payload": payload,
                    "response": parsed_body,
                }

            errors.append(
                {
                    "url": url,
                    "payload": payload,
                    "status_code": int(response.status_code),
                    "response": parsed_body,
                }
            )

    detail = "tabbyapi native unload endpoint unavailable"
    if errors:
        last = errors[-1]
        status_code = last.get("status_code")
        if isinstance(status_code, int):
            detail = f"tabbyapi native unload failed with status {status_code}"
        elif last.get("error"):
            detail = f"tabbyapi native unload request error: {last.get('error')}"

    return {
        "ok": False,
        "method": "tabbyapi_native_unload",
        "mode": mode_key,
        "base": base,
        "base_source": base_source,
        "detail": detail,
        "attempts": len(errors),
        "errors": errors[-3:],
    }


def _readiness_timeout_seconds(requested: float | None = None) -> float:
    if requested is not None:
        value = float(requested)
    else:
        value = float(read_env().get("MODEL_READINESS_TIMEOUT_SECONDS", "90") or 90)
    if value <= 0:
        raise HTTPException(400, "readiness_timeout_seconds must be > 0")
    return min(value, 300.0)


def _wait_for_model_readiness(
    mode: str,
    expected_model: Path,
    backend: str,
    timeout_seconds: float | None = None,
) -> dict:
    timeout = _readiness_timeout_seconds(timeout_seconds)
    env = read_env()
    provider_models = read_provider_models()
    base, base_source = _local_base_for_mode(mode, env, backend=backend, provider_models=provider_models)
    url = str(base).rstrip("/") + "/v1/models"
    relative_name = str(expected_model.resolve().relative_to(MODELS_DIR.resolve()))
    accepted_ids = {str(expected_model.resolve()), relative_name, expected_model.name}
    deadline = time.monotonic() + timeout
    attempts = 0
    last_status = None
    last_model_ids: list[str] = []
    last_error = None

    while True:
        attempts += 1
        remaining = max(0.0, deadline - time.monotonic())
        try:
            response = requests.get(url, timeout=max(0.05, min(2.0, remaining or 0.05)))
            last_status = int(response.status_code)
            if response.ok:
                body = response.json()
                rows = body.get("data", []) if isinstance(body, dict) else []
                last_model_ids = [
                    str(row.get("id", ""))
                    for row in rows
                    if isinstance(row, dict) and str(row.get("id", ""))
                ]
                matched = next((model_id for model_id in last_model_ids if model_id in accepted_ids), None)
                if matched is not None:
                    return {
                        "ok": True,
                        "mode": mode,
                        "backend": backend,
                        "url": url,
                        "base_source": base_source,
                        "expected_model_ids": sorted(accepted_ids),
                        "matched_model_id": matched,
                        "model_ids": last_model_ids,
                        "attempts": attempts,
                    }
                last_error = "endpoint is healthy but reports a different active model"
            else:
                last_error = f"model endpoint returned HTTP {response.status_code}"
        except Exception as exc:
            last_error = str(exc)

        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "mode": mode,
                "backend": backend,
                "url": url,
                "base_source": base_source,
                "expected_model_ids": sorted(accepted_ids),
                "model_ids": last_model_ids,
                "status_code": last_status,
                "attempts": attempts,
                "timeout_seconds": timeout,
                "detail": last_error or "model readiness timed out",
            }
        time.sleep(min(0.5, max(0.01, deadline - time.monotonic())))

# -----------------------------------------------------------------------------
# Core: model switch
# -----------------------------------------------------------------------------
@_model_lifecycle_transactional
def switch_model(
    mode: str,
    model_dir: str,
    bounce: bool,
    backend: str | None = None,
    lifecycle_mode: str | None = None,
    native_max_seq_len: int | None = None,
    require_loaded: bool = False,
    readiness_timeout_seconds: float | None = None,
):
    # Historical UI used 'util' for the third slot -> treat as small.
    if mode == "util":
        mode = "small"
    if mode not in SLOT_MODES:
        raise HTTPException(400, "mode must be chat|intent|small|embed|util")

    if backend is not None and backend not in SUPPORTED_BACKENDS:
        raise HTTPException(400, f"backend must be one of: {', '.join(sorted(SUPPORTED_BACKENDS))}")

    normalized_lifecycle_mode = _normalize_switch_lifecycle_mode(lifecycle_mode)
    if native_max_seq_len is not None and int(native_max_seq_len) <= 0:
        raise HTTPException(400, "native_max_seq_len must be > 0 when provided")

    target = _resolve_model_directory(model_dir)
    model_name = str(target.relative_to(MODELS_DIR.resolve()))

    inspection = inspect_one(model_name)
    model_kind = str(inspection.get("kind", "unknown") or "unknown")
    raw_model_capabilities = inspection.get("capabilities", []) if isinstance(inspection.get("capabilities"), list) else []
    model_capabilities = {
        str(value).strip().lower()
        for value in raw_model_capabilities
        if str(value).strip()
    }
    required_model_capability = "embeddings" if mode == "embed" else "chat"
    if required_model_capability not in model_capabilities:
        raise HTTPException(
            422,
            f"model '{model_dir}' does not declare the required '{required_model_capability}' capability for slot '{mode}'",
        )
    recommended_backend = str(inspection.get("recommended_backend", "") or "").strip().lower()
    unsupported_reason = str(inspection.get("unsupported_reason", "") or "").strip() or None
    fallback_backends = [
        str(b).strip().lower()
        for b in (inspection.get("fallback_backends", []) if isinstance(inspection.get("fallback_backends", []), list) else [])
        if str(b).strip()
    ]
    compatible_backends = [
        candidate
        for candidate in ("tgw", "vllm", "tabbyapi")
        if _backend_supports_model_kind(candidate, model_kind) and (mode != "embed" or candidate == "vllm")
    ]

    if not compatible_backends:
        detail = (
            unsupported_reason
            or f"model kind '{model_kind}' has no compatible local backend"
        )
        raise HTTPException(
            422,
            f"model '{model_dir}' cannot be loaded in this deployment: {detail}",
        )

    link = {
        "chat":   MODELS_DIR / "chat_active_model",
        "intent": MODELS_DIR / "intent_active_model",
        "small":  MODELS_DIR / "small_active_model",
        "embed":  MODELS_DIR / "embed_active_model",
    }[mode]

    slot_backends_before = read_slot_backends()
    previous_backend = str(slot_backends_before.get(mode, DEFAULT_SLOT_BACKENDS.get(mode, "tgw")) or "tgw")
    chosen_backend = backend
    backend_source = "request" if backend is not None else "existing"
    auto_backend_applied = False
    if chosen_backend is None and recommended_backend in SUPPORTED_BACKENDS:
        chosen_backend = recommended_backend
        backend_source = "auto_recommended"
        auto_backend_applied = recommended_backend != previous_backend

    if chosen_backend is None:
        chosen_backend = previous_backend

    if mode == "embed" and chosen_backend != "vllm":
        if backend is not None:
            raise HTTPException(400, "the embedding slot requires backend=vllm")
        chosen_backend = "vllm"
        backend_source = "embedding_lane_required"
        auto_backend_applied = chosen_backend != previous_backend

    # Avoid selecting backends for incompatible model formats.
    if not _backend_supports_model_kind(chosen_backend, model_kind):
        if backend is not None:
            raise HTTPException(
                400,
                f"backend '{chosen_backend}' is incompatible with model kind '{model_kind}'. "
                f"Use backend '{recommended_backend or 'tgw'}' for this model.",
            )
        if recommended_backend in SUPPORTED_BACKENDS and _backend_supports_model_kind(recommended_backend, model_kind):
            chosen_backend = recommended_backend
        else:
            chosen_backend = "tgw"
        backend_source = "auto_compatible_fallback"
        auto_backend_applied = chosen_backend != previous_backend

    # Guard vllm lanes when vllm is not available in runtime.
    if chosen_backend == "vllm" and not _vllm_backend_available():
        if mode == "embed":
            raise HTTPException(400, "the embedding slot requires an available vllm runtime")
        if backend is not None:
            raise HTTPException(400, "backend 'vllm' is not available in the current runtime (missing vllm module)")
        fallback_choice = None
        for candidate in fallback_backends:
            if candidate in SUPPORTED_BACKENDS and candidate != "vllm" and _backend_supports_model_kind(candidate, model_kind):
                fallback_choice = candidate
                break
        chosen_backend = fallback_choice or "tgw"
        backend_source = "auto_vllm_unavailable_fallback"
        auto_backend_applied = chosen_backend != previous_backend

    slot_backend = str(chosen_backend or previous_backend)
    native_load_attempted = False
    native_load_used = False
    native_load: dict | None = None
    bounce_result: dict | None = None
    rollback_result: dict | None = None
    readiness: dict | None = None
    if link.exists() and not link.is_symlink():
        raise HTTPException(409, f"active model path is not a managed symlink: {link}")
    previous_target = _symlink_target(link)
    if previous_target is not None:
        models_root = MODELS_DIR.resolve()
        if previous_target != models_root and not previous_target.is_relative_to(models_root):
            raise HTTPException(409, f"active model symlink points outside the managed models directory: {link}")

    if normalized_lifecycle_mode == "native" and slot_backend != "tabbyapi":
        raise HTTPException(400, "lifecycle_mode=native requires backend=tabbyapi for the selected slot")

    should_try_native = normalized_lifecycle_mode == "native" or (
        normalized_lifecycle_mode == "auto" and slot_backend == "tabbyapi"
    )

    if should_try_native:
        native_load_attempted = True
        native_load = _tabbyapi_native_load(mode, target, max_seq_len=native_max_seq_len)
        if native_load.get("ok"):
            native_load_used = True
        elif normalized_lifecycle_mode == "native":
            detail = str(native_load.get("detail", "unknown error"))
            raise HTTPException(502, f"tabbyapi native load failed: {detail}")

    if require_loaded and not native_load_used and not bounce:
        detail = "selected backend requires an engine restart to load the model"
        if native_load_attempted and isinstance(native_load, dict):
            detail = str(native_load.get("detail") or detail)
        raise HTTPException(409, f"model was not loaded: {detail}; set bounce=true or use a working native lifecycle")

    try:
        _make_symlink(link, target)
        set_slot_backend(mode, slot_backend)
        if bounce and not native_load_used:
            bounce_result = _bounce_engine(mode)
        if native_load_used or bounce:
            readiness = _wait_for_model_readiness(
                mode,
                target,
                slot_backend,
                timeout_seconds=readiness_timeout_seconds,
            )
            if not readiness.get("ok"):
                raise HTTPException(504, {"message": "new model did not become ready", "readiness": readiness})
    except Exception as exc:
        rollback_errors = []
        try:
            _restore_symlink(link, previous_target)
        except Exception as rollback_exc:
            rollback_errors.append(f"symlink: {rollback_exc}")
        try:
            set_slot_backend(mode, previous_backend)
        except Exception as rollback_exc:
            rollback_errors.append(f"backend: {rollback_exc}")
        if bounce and not native_load_used:
            try:
                rollback_result = _bounce_engine(mode) if previous_target else _stop_engine(mode)
            except Exception as rollback_exc:
                rollback_errors.append(f"engine: {rollback_exc}")
        if native_load_used:
            if previous_target is not None and previous_backend == "tabbyapi":
                restore_native = _tabbyapi_native_load(mode, previous_target)
                rollback_result = restore_native
                if not restore_native.get("ok"):
                    rollback_errors.append(f"native restore: {restore_native.get('detail', 'failed')}")
            else:
                unload_result = _tabbyapi_native_unload(mode)
                if not unload_result.get("ok"):
                    rollback_errors.append(f"native unload: {unload_result.get('detail', 'failed')}")
                if previous_target is not None:
                    try:
                        rollback_result = _bounce_engine(mode)
                    except Exception as rollback_exc:
                        rollback_errors.append(f"engine: {rollback_exc}")
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        status_code = exc.status_code if isinstance(exc, HTTPException) else 500
        raise HTTPException(status_code, {
            "message": "model switch failed and previous slot state was restored",
            "cause": detail,
            "rollback_errors": rollback_errors,
            "previous_backend": previous_backend,
            "previous_target": str(previous_target) if previous_target else None,
        }) from exc

    return {
        "ok": True,
        "link": str(link),
        "target": str(target),
        "mode": mode,
        "backend": slot_backend,
        "backend_source": backend_source,
        "auto_backend_applied": auto_backend_applied,
        "previous_backend": previous_backend,
        "model_kind": model_kind,
        "model_capabilities": sorted(model_capabilities),
        "compatible_backends": compatible_backends,
        "unsupported_reason": unsupported_reason,
        "recommended_backend": recommended_backend if recommended_backend in SUPPORTED_BACKENDS else None,
        "fallback_backends": fallback_backends,
        "lifecycle_mode": normalized_lifecycle_mode,
        "native_load_attempted": native_load_attempted,
        "native_load_used": native_load_used,
        "native_load": native_load,
        "loaded": bool(native_load_used or bounce),
        "load_method": "tabbyapi_native" if native_load_used else ("engine_restart" if bounce else "staged"),
        "bounce": bool(bounce),
        "bounce_result": bounce_result,
        "readiness": readiness,
        "rollback_result": rollback_result,
    }

# -----------------------------------------------------------------------------
# Jobs: tiny runner for train/merge/convert
# -----------------------------------------------------------------------------
JOBS: dict[str, dict] = {}
JOB_PROCESSES: dict[str, subprocess.Popen] = {}
JOB_PIDFDS: dict[str, int] = {}
JOB_IDENTITIES: dict[str, dict] = {}
JOB_LOCK = threading.RLock()


def _boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() or None
    except OSError:
        return None


def _process_identity(pid: int) -> dict | None:
    try:
        stat_text = Path(f"/proc/{int(pid)}/stat").read_text()
        remainder = stat_text.rsplit(")", 1)[1].strip().split()
        process_start_ticks = int(remainder[19])
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, ValueError, IndexError):
        return None
    return {
        "process_start_ticks": process_start_ticks,
        "boot_id": _boot_id(),
        "command_sha256": hashlib.sha256(command).hexdigest(),
    }


def _same_process_identity(expected: dict, actual: dict | None) -> bool:
    if not isinstance(actual, dict):
        return False
    return all(
        expected.get(key) is not None and expected.get(key) == actual.get(key)
        for key in ("process_start_ticks", "boot_id", "command_sha256")
    )


def _persist_job(job_id: str) -> None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not isinstance(job, dict):
            return
        payload = redact_sensitive_data(dict(job))
        identity = dict(JOB_IDENTITIES.get(job_id, {}))
    _runtime_store().upsert_job(payload, identity=identity)


@_state_transactional
def _mark_conversion_job_interrupted(job_id: str, reason: str) -> None:
    state = read_provider_runtime_state()
    runs = state.get("conversion_runs", {}) if isinstance(state.get("conversion_runs"), dict) else {}
    row = runs.get(job_id)
    if not isinstance(row, dict) or str(row.get("status")) not in {"running", "cancelling"}:
        return
    now = datetime.utcnow().isoformat() + "Z"
    row["status"] = "interrupted"
    row["error"] = reason
    row["completed_ts"] = now
    row["updated_ts"] = now
    runs[job_id] = row
    state["conversion_runs"] = runs
    write_provider_runtime_state(state)


def _watch_recovered_job(job_id: str, pidfd: int) -> None:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    try:
        poller.poll()
    except (OSError, ValueError):
        return
    with JOB_LOCK:
        if JOB_PIDFDS.get(job_id) != pidfd:
            return
        job = JOBS.get(job_id)
        if isinstance(job, dict):
            job["status"] = "ended_unknown"
            job["end_ts"] = time.time()
            job["recovery"] = {"state": "ended_after_restart", "returncode_available": False}
        JOB_PIDFDS.pop(job_id, None)
        JOB_IDENTITIES.pop(job_id, None)
        try:
            os.close(pidfd)
        except OSError:
            pass
    _persist_job(job_id)
    _mark_conversion_job_interrupted(job_id, "process ended after API restart; return code unavailable")


def _recover_managed_jobs() -> dict:
    recovered = interrupted = finished = 0
    rows = _runtime_store().list_jobs()
    with JOB_LOCK:
        for stored in rows:
            identity = stored.pop("_process_identity", {})
            job_id = str(stored.get("id", ""))
            if not job_id:
                continue
            JOBS[job_id] = stored
            status = str(stored.get("status", ""))
            if status not in {"running", "cancelling", "recovered_running"}:
                finished += 1
                continue
            pid = int(stored.get("pid", 0) or 0)
            actual_before = _process_identity(pid) if pid > 0 else None
            if not _same_process_identity(identity, actual_before) or not hasattr(os, "pidfd_open"):
                stored["status"] = "interrupted"
                stored["end_ts"] = time.time()
                stored["recovery"] = {"state": "process_identity_not_live"}
                interrupted += 1
                continue
            try:
                pidfd = os.pidfd_open(pid, 0)
            except OSError:
                stored["status"] = "interrupted"
                stored["end_ts"] = time.time()
                stored["recovery"] = {"state": "pidfd_open_failed"}
                interrupted += 1
                continue
            actual_after = _process_identity(pid)
            if not _same_process_identity(identity, actual_after):
                os.close(pidfd)
                stored["status"] = "interrupted"
                stored["end_ts"] = time.time()
                stored["recovery"] = {"state": "process_identity_changed"}
                interrupted += 1
                continue
            stored["status"] = "recovered_running"
            stored["recovery"] = {"state": "reattached_with_pidfd", "returncode_available": False}
            JOB_PIDFDS[job_id] = pidfd
            JOB_IDENTITIES[job_id] = identity
            recovered += 1

    for stored in rows:
        job_id = str(stored.get("id", ""))
        if job_id:
            _persist_job(job_id)
            if str(stored.get("status")) == "interrupted":
                _mark_conversion_job_interrupted(job_id, "process was not live during API startup reconciliation")
    with JOB_LOCK:
        recovered_fds = [(job_id, fd) for job_id, fd in JOB_PIDFDS.items() if JOBS.get(job_id, {}).get("status") == "recovered_running"]
    for job_id, fd in recovered_fds:
        threading.Thread(target=_watch_recovered_job, args=(job_id, fd), daemon=True).start()
    runtime_state = read_provider_runtime_state()
    conversion_runs = (
        runtime_state.get("conversion_runs", {})
        if isinstance(runtime_state.get("conversion_runs"), dict)
        else {}
    )
    for job_id, row in conversion_runs.items():
        if (
            isinstance(row, dict)
            and str(row.get("status")) in {"running", "cancelling"}
            and str(JOBS.get(str(job_id), {}).get("status", "")) not in {"running", "cancelling", "recovered_running"}
        ):
            _mark_conversion_job_interrupted(
                str(job_id),
                "legacy running conversion had no persistent managed-job identity during upgrade",
            )
            interrupted += 1
    return {"recovered_running": recovered, "interrupted": interrupted, "finished": finished}


def _startup_runtime_reconciliation() -> None:
    _runtime_store().schema_version()
    _recover_evaluation_jobs()
    _recover_managed_jobs()
    if _env_flag(os.getenv("EVAL_WORKER_AUTOSTART"), True):
        _ensure_eval_worker_started()


def _validate_job_path_args(kind: str, args: dict, env: dict) -> dict:
    validated = dict(args)
    if str(validated.get("data_path") or "").strip():
        validated["data_path"] = str(_resolve_path_within(
            validated["data_path"],
            [ROOT / "data"],
            must_exist=True,
            label="data_path",
        ))

    if str(validated.get("source_model_dir") or "").strip():
        validated["source_model_dir"] = str(_resolve_path_within(
            validated["source_model_dir"],
            [ROOT / "output", MODELS_DIR.resolve()],
            must_exist=True,
            must_be_dir=True,
            label="source_model_dir",
        ))
    if str(validated.get("output_dir") or "").strip():
        validated["output_dir"] = str(_resolve_path_within(
            validated["output_dir"],
            [ROOT / "output", MODELS_DIR.resolve()],
            label="output_dir",
        ))

    for field, configured in (
        ("base_models_dir", env.get("BASE_MODELS_DIR") or (ENGINES_ROOT / "models")),
        ("webui_models_dir", env.get("WEBUI_MODELS_DIR") or MODELS_DIR),
    ):
        if str(validated.get(field) or "").strip():
            configured_path = Path(str(configured)).resolve()
            validated[field] = str(_resolve_path_within(
                validated[field],
                [configured_path, ENGINES_ROOT],
                label=field,
            ))

    if str(validated.get("exllama_root") or "").strip():
        validated["exllama_root"] = str(_resolve_path_within(
            validated["exllama_root"],
            [ENGINES_ROOT],
            must_exist=True,
            must_be_dir=True,
            label="exllama_root",
        ))

    for field in ("convert_script", "exl3_convert_script"):
        if str(validated.get(field) or "").strip():
            script_path = _resolve_path_within(
                validated[field],
                [ENGINES_ROOT],
                must_exist=True,
                label=field,
            )
            if not script_path.is_file() or script_path.suffix != ".py":
                raise HTTPException(422, f"{field} must be an existing Python file under {ENGINES_ROOT}")
            validated[field] = str(script_path)

    if kind.startswith("convert_hf_"):
        validated["repo_id"] = _validate_repo_id(str(validated.get("repo_id") or ""))
    return validated

def _tail(path: Path, n: int = 120) -> list[str]:
    if not path.exists():
        return []
    try:
        out = subprocess.check_output(["tail", "-n", str(n), str(path)], stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return path.read_text(errors="replace").splitlines()[-n:]


def _safe_repo_folder_name(repo_id: str) -> str:
    return _validate_repo_id(repo_id).replace("/", "__")


def _as_positive_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return parsed if parsed > 0 else default


def _as_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return parsed if parsed > 0 else default


def _conversion_paths_for_hf(repo_id: str, bits: float, args: dict, env: dict, target_format: str = "exl2") -> tuple[Path, Path]:
    configured_base = Path(str(env.get("BASE_MODELS_DIR") or (ROOT / "models"))).resolve()
    configured_webui = Path(str(env.get("WEBUI_MODELS_DIR") or MODELS_DIR)).resolve()
    base_models_dir = _resolve_path_within(
        args.get("base_models_dir") or configured_base,
        [configured_base],
        label="base_models_dir",
    )
    webui_models_dir = _resolve_path_within(
        args.get("webui_models_dir") or configured_webui,
        [configured_webui, MODELS_DIR.resolve()],
        label="webui_models_dir",
    )
    safe_name = _safe_repo_folder_name(repo_id)
    bits_tag = str(bits).replace(".", "p")
    format_name = str(target_format or "exl2").strip().lower() or "exl2"
    return base_models_dir / safe_name, webui_models_dir / f"{safe_name}_{format_name}_b{bits_tag}"


def _conversion_paths_for_merged(model_key: str, args: dict, target_format: str = "exl2") -> tuple[Path, Path]:
    source_model_dir = str(args.get("source_model_dir") or "").strip()
    output_dir = str(args.get("output_dir") or "").strip()

    source_path = _resolve_path_within(
        source_model_dir or (ROOT / "output" / f"merged_{model_key}"),
        [ROOT / "output", MODELS_DIR.resolve()],
        must_exist=True,
        must_be_dir=True,
        label="source_model_dir",
    )
    format_name = str(target_format or "exl2").strip().lower() or "exl2"
    default_output = f"lora_{model_key}" if format_name == "exl2" else f"lora_{model_key}_{format_name}"
    output_path = _resolve_path_within(
        output_dir or (ROOT / "output" / default_output),
        [ROOT / "output", MODELS_DIR.resolve()],
        label="output_dir",
    )
    return source_path, output_path


def _normalized_conversion_format(value: object, default: str = "exl2") -> str:
    text = str(value or default).strip().lower()
    return text if text in {"exl2", "exl3"} else default


CONVERSION_KIND_FORMAT = {
    "convert_hf_exl2": "exl2",
    "convert_merged_exl2": "exl2",
    "convert_hf_exl3": "exl3",
    "convert_merged_exl3": "exl3",
}


def _conversion_kind_format(kind: str) -> str:
    return CONVERSION_KIND_FORMAT.get(str(kind or "").strip(), "")


def _conversion_kind_source_type(kind: str) -> str:
    kind_text = str(kind or "").strip()
    if kind_text.startswith("convert_hf_"):
        return "huggingface_repo"
    if kind_text.startswith("convert_merged_"):
        return "merged_local_model"
    return str(kind_text)


def _conversion_kinds_for_format(target_format: str) -> set[str]:
    normalized = _normalized_conversion_format(target_format, default="exl2")
    return {kind for kind, fmt in CONVERSION_KIND_FORMAT.items() if fmt == normalized}


def _resolve_exl3_convert_script(args: dict, env: dict) -> tuple[Path | None, list[str]]:
    checked: list[str] = []

    explicit_script = str(args.get("exl3_convert_script") or args.get("convert_script") or "").strip()
    if explicit_script:
        path = Path(explicit_script)
        checked.append(str(path))
        if path.exists() and path.is_file():
            return path, checked

    explicit_root = str(args.get("exllama_root") or "").strip()
    env_root = str(env.get("EXLLAMA_V3_ROOT") or "").strip()
    default_root = "/srv/2bananas/engines/exllamav3"
    for root in (explicit_root, env_root, default_root):
        if not root:
            continue
        root_path = Path(root)
        if root_path.is_file():
            checked.append(str(root_path))
            if root_path.exists():
                return root_path, checked
            continue
        for rel_path in ("convert.py", "exllamav3/convert.py"):
            candidate = root_path / rel_path
            checked.append(str(candidate))
            if candidate.exists() and candidate.is_file():
                return candidate, checked

    env_script = str(env.get("EXL3_CONVERT_SCRIPT") or env.get("EXLLAMA_CONVERT_SCRIPT") or "").strip()
    if env_script:
        path = Path(env_script)
        checked.append(str(path))
        if path.exists() and path.is_file():
            return path, checked

    return None, checked


def _safe_read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(errors="replace"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _chat_template_in_model_dir(model_dir: Path) -> tuple[str | None, str | None]:
    for filename in ("tokenizer_config.json", "config.json", "generation_config.json"):
        payload = _safe_read_json_file(model_dir / filename)
        template = payload.get("chat_template")
        if isinstance(template, str) and template.strip():
            return template.strip(), filename
    return None, None


def _conversion_source_sha(output_dir: Path) -> str:
    hash_files = [
        output_dir / "source_model_sha256.txt",
        output_dir / "converted_from_merge_hash.txt",
    ]
    for path in hash_files:
        if not path.exists():
            continue
        try:
            value = path.read_text(errors="replace").strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def _conversion_preservation_checks(source_dir: Path, output_dir: Path) -> dict:
    file_rows = []
    missing = []
    expected = 0
    copied = 0

    for filename in CONVERSION_TOKENIZER_FILES:
        source_exists = (source_dir / filename).exists()
        output_exists = (output_dir / filename).exists()
        if source_exists:
            expected += 1
            if output_exists:
                copied += 1
            else:
                missing.append(filename)
        file_rows.append({
            "name": filename,
            "source_exists": source_exists,
            "output_exists": output_exists,
        })

    source_template, source_template_file = _chat_template_in_model_dir(source_dir)
    output_template, output_template_file = _chat_template_in_model_dir(output_dir)
    source_has_template = bool(source_template)
    output_has_template = bool(output_template)
    template_preserved = None
    if source_has_template:
        template_preserved = bool(output_has_template and source_template == output_template)

    ok = (not missing) and (template_preserved is not False)
    return {
        "ok": ok,
        "tokenizer_artifacts_expected": expected,
        "tokenizer_artifacts_present": copied,
        "missing_tokenizer_artifacts": missing,
        "chat_template_source_present": source_has_template,
        "chat_template_output_present": output_has_template,
        "chat_template_preserved": template_preserved,
        "source_chat_template_file": source_template_file,
        "output_chat_template_file": output_template_file,
        "files": file_rows,
    }


def _trim_conversion_runs(runs: dict[str, dict]) -> dict[str, dict]:
    rows = [row for row in runs.values() if isinstance(row, dict) and str(row.get("id", ""))]
    rows.sort(key=lambda row: str(row.get("created_ts", "")), reverse=True)
    trimmed: dict[str, dict] = {}
    for row in rows[:MAX_CONVERSION_RUNS]:
        run_id = str(row.get("id", ""))
        if run_id:
            trimmed[run_id] = row
    return trimmed


def _upsert_local_converted_catalog_entry(artifact: dict) -> dict:
    try:
        doc = read_provider_models()
        local = doc.get("local")
        if not isinstance(local, dict):
            local = {}
            doc["local"] = local

        rows = local.get("converted_models")
        if not isinstance(rows, list):
            rows = []

        model_ref = str(artifact.get("model_ref") or artifact.get("output_model_dir") or artifact.get("artifact_id"))
        target_format = _normalized_conversion_format(artifact.get("format"), default="exl2")
        entry = {
            "id": model_ref,
            "label": str(artifact.get("source_repo_id") or model_ref),
            "enabled": True,
            "backend": str(artifact.get("recommended_backend") or "tabbyapi"),
            "format": target_format,
            "model_dir": str(artifact.get("output_model_dir") or ""),
            "path": str(artifact.get("output_dir") or ""),
            "source_type": str(artifact.get("source_type") or ""),
            "source_repo_id": str(artifact.get("source_repo_id") or ""),
            "source_sha256": str(artifact.get("source_sha256") or ""),
            "bits": artifact.get("bits"),
            "groupsize": artifact.get("groupsize"),
            "created_ts": str(artifact.get("created_ts") or ""),
            "artifact_id": str(artifact.get("artifact_id") or ""),
            "job_id": str(artifact.get("job_id") or ""),
            "notes": f"Managed {target_format.upper()} conversion artifact",
        }

        updated = False
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            if str(row.get("id", "")) == model_ref:
                merged = dict(row)
                for key, value in entry.items():
                    if value not in ("", None):
                        merged[key] = value
                rows[idx] = merged
                updated = True
                break

        if not updated:
            rows.append(entry)
            updated = True

        rows.sort(key=lambda row: str((row or {}).get("created_ts", "")), reverse=True)
        local["converted_models"] = rows[:500]
        _write_json(PROVIDER_MODELS_PATH, doc)
        return {"ok": True, "updated": updated, "model_ref": model_ref}
    except Exception as e:
        return {"ok": False, "updated": False, "error": str(e)}


def _build_conversion_artifact_record(run_row: dict) -> dict:
    output_dir = Path(str(run_row.get("output_dir") or ""))
    source_model_dir = Path(str(run_row.get("source_model_dir") or ""))
    source_type = str(run_row.get("source_type") or "huggingface_repo")
    target_format = _normalized_conversion_format(run_row.get("format"), default="exl2")
    output_model_dir = str(run_row.get("output_model_dir") or output_dir.name)
    source_repo_id = str(run_row.get("source_repo_id") or run_row.get("model_key") or "")
    default_bits = 6.5 if target_format == "exl2" else 4.5
    bits = _as_positive_float(run_row.get("bits"), default_bits)
    groupsize_value = run_row.get("groupsize")
    groupsize = _as_positive_int(groupsize_value, 2048) if (target_format == "exl2" or groupsize_value not in (None, "")) else None

    source_sha = _conversion_source_sha(output_dir)
    preservation_checks = _conversion_preservation_checks(source_model_dir, output_dir)

    output_size_bytes = 0
    if output_dir.exists() and output_dir.is_dir():
        try:
            for item in output_dir.rglob("*"):
                if item.is_file():
                    output_size_bytes += int(item.stat().st_size)
        except Exception:
            output_size_bytes = 0

    detected_kind = "unknown"
    loader = "transformers"
    if output_dir.exists() and output_dir.is_dir():
        try:
            detected_kind = detect_kind(output_dir)
        except Exception:
            detected_kind = "unknown"
        try:
            loader = detect_loader(detected_kind)
        except Exception:
            loader = "transformers"

    artifact_key = f"{source_type}-{target_format}:{source_repo_id}:{bits}:{groupsize or ''}:{output_dir}"
    artifact_id = hashlib.sha256(artifact_key.encode("utf-8")).hexdigest()[:16]
    created_ts = datetime.utcnow().isoformat() + "Z"
    recommended_backend = "tabbyapi" if detected_kind in {"exl2", "exl3"} else "tgw"

    artifact_status = "missing_output"
    if output_dir.exists():
        artifact_status = "ready" if bool(preservation_checks.get("ok", False)) else "ready_with_warnings"

    return {
        "artifact_id": artifact_id,
        "job_id": str(run_row.get("job_id") or run_row.get("id") or ""),
        "status": artifact_status,
        "source_type": source_type,
        "source_repo_id": source_repo_id,
        "source_model_dir": str(run_row.get("source_model_dir") or ""),
        "source_sha256": source_sha,
        "format": target_format,
        "bits": bits,
        "groupsize": groupsize,
        "output_dir": str(output_dir),
        "output_model_dir": output_model_dir,
        "output_size_bytes": output_size_bytes,
        "detected_kind": detected_kind,
        "recommended_backend": recommended_backend,
        "loader": loader,
        "model_ref": output_model_dir,
        "preservation_checks": preservation_checks,
        "created_ts": created_ts,
        "updated_ts": created_ts,
    }


@_state_transactional
def _record_conversion_run_start(job_id: str, kind: str, args: dict, env: dict, log_path: Path, pid: int) -> dict:
    source_type = _conversion_kind_source_type(kind) or str(args.get("source_type") or "").strip().lower()
    target_format = _conversion_kind_format(kind) or _normalized_conversion_format(args.get("target_format"), default="exl2")

    default_bits = 6.5 if target_format == "exl2" else 4.5
    bits = _as_positive_float(args.get("bits"), default_bits)
    groupsize_raw = args.get("groupsize")
    groupsize = _as_positive_int(groupsize_raw, 2048) if (target_format == "exl2" or groupsize_raw not in (None, "")) else None
    source_repo_id = ""
    model_key = ""
    if source_type == "merged_local_model":
        model_key = str(args.get("model_key") or "").strip()
        source_model_dir, output_dir = _conversion_paths_for_merged(model_key, args, target_format=target_format)
        source_repo_id = model_key
    else:
        repo_id = str(args.get("repo_id") or "").strip()
        source_model_dir, output_dir = _conversion_paths_for_hf(repo_id, bits, args, env, target_format=target_format)
        source_repo_id = repo_id

    now_iso = datetime.utcnow().isoformat() + "Z"

    row = {
        "id": job_id,
        "job_id": job_id,
        "kind": kind,
        "status": "running",
        "created_ts": now_iso,
        "started_ts": now_iso,
        "updated_ts": now_iso,
        "pid": int(pid),
        "log": str(log_path),
        "source_type": source_type,
        "source_repo_id": source_repo_id,
        "model_key": model_key,
        "source_model_dir": str(source_model_dir),
        "format": target_format,
        "bits": bits,
        "groupsize": groupsize,
        "force": bool(args.get("force", False)),
        "output_dir": str(output_dir),
        "output_model_dir": output_dir.name,
        "preservation_checks": None,
        "catalog_sync": {"ok": False, "updated": False},
    }

    with CONVERSION_STATE_LOCK:
        state = read_provider_runtime_state()
        runs = state.get("conversion_runs", {}) if isinstance(state.get("conversion_runs"), dict) else {}
        runs[job_id] = row
        state["conversion_runs"] = _trim_conversion_runs(runs)
        write_provider_runtime_state(state)
    return row


@_state_transactional
def _record_conversion_run_finish(job_id: str, returncode: int):
    with CONVERSION_STATE_LOCK:
        state = read_provider_runtime_state()
        runs = state.get("conversion_runs", {}) if isinstance(state.get("conversion_runs"), dict) else {}
        row = runs.get(job_id)
        if not isinstance(row, dict):
            return

        now_iso = datetime.utcnow().isoformat() + "Z"
        row["status"] = "completed" if int(returncode) == 0 else "error"
        row["returncode"] = int(returncode)
        row["completed_ts"] = now_iso
        row["updated_ts"] = now_iso

        if int(returncode) == 0:
            source_model_dir = Path(str(row.get("source_model_dir") or ""))
            output_dir = Path(str(row.get("output_dir") or ""))
            checks = _conversion_preservation_checks(source_model_dir, output_dir)
            row["preservation_checks"] = checks
            if not bool(checks.get("ok", False)):
                row["status"] = "completed_with_warnings"

            artifact = _build_conversion_artifact_record(row)
            artifacts = state.get("conversion_artifacts", {}) if isinstance(state.get("conversion_artifacts"), dict) else {}
            artifacts[str(artifact["artifact_id"])] = artifact
            state["conversion_artifacts"] = artifacts
            row["artifact_id"] = artifact["artifact_id"]
            row["catalog_sync"] = _upsert_local_converted_catalog_entry(artifact)

        runs[job_id] = row
        state["conversion_runs"] = _trim_conversion_runs(runs)
        write_provider_runtime_state(state)


def _conversion_run_by_job_id(job_id: str) -> dict | None:
    state = read_provider_runtime_state()
    runs = state.get("conversion_runs", {}) if isinstance(state.get("conversion_runs"), dict) else {}
    row = runs.get(job_id)
    return row if isinstance(row, dict) else None


def _conversion_artifact_rows(format_filter: str | None = None) -> list[dict]:
    state = read_provider_runtime_state()
    artifacts = state.get("conversion_artifacts", {}) if isinstance(state.get("conversion_artifacts"), dict) else {}
    rows = [row for row in artifacts.values() if isinstance(row, dict)]
    if format_filter:
        rows = [row for row in rows if str(row.get("format", "")).lower() == str(format_filter).lower()]
    rows.sort(key=lambda row: str(row.get("created_ts", "")), reverse=True)
    return rows

def _launch_job(kind: str, args: dict) -> dict:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    log_path = LOGS_DIR / f"{int(time.time())}_{kind}_{job_id}.log"
    conversion_kinds = set(CONVERSION_KIND_FORMAT.keys())

    runtime_env = read_env()
    args = _validate_job_path_args(kind, args, runtime_env)
    env = os.environ.copy()
    conversion_python_bin = str(runtime_env.get("CONVERSION_PYTHON_BIN") or "").strip()
    base_python_cmd = "python3"
    if kind in conversion_kinds and conversion_python_bin:
        base_python_cmd = conversion_python_bin
    elif sys.executable:
        base_python_cmd = str(sys.executable)

    env["CUDA_VISIBLE_DEVICES"] = runtime_env.get("CUDA_VISIBLE_DEVICES", "0")
    for env_key in ("BASE_MODELS_DIR", "WEBUI_MODELS_DIR", "EXLLAMA_ROOT", "EXLLAMA_V3_ROOT", "EXL3_CONVERT_SCRIPT", "HF_TOKEN"):
        value = runtime_env.get(env_key)
        if value:
            env[env_key] = str(value)

    script_map = {
        "train":   SCRIPTS_DIR / "train_lora.py",
        "merge":   SCRIPTS_DIR / "merge_lora.py",
        "convert": SCRIPTS_DIR / "convert_lora.py",
        "convert_hf_exl2": SCRIPTS_DIR / "download_convert_chat_model.py",
        "convert_merged_exl2": SCRIPTS_DIR / "convert_lora.py",
        "convert_hf_exl3": SCRIPTS_DIR / "download_convert_chat_model.py",
        "convert_merged_exl3": SCRIPTS_DIR / "convert_lora.py",
    }
    script = script_map.get(kind)
    if not script or not script.exists():
        raise HTTPException(404, f"{kind}_script not found: {script}")

    cmd = None
    if kind == "train":
        if args.get("train_all"):
            cmd = [base_python_cmd, str(script), "--train_all"]
        else:
            mk = args.get("model_key")
            if not mk:
                raise HTTPException(400, "model_key required for train (or set train_all)")
            cmd = [base_python_cmd, str(script), "--model_key", mk]
        if args.get("force"):
            cmd.append("--force")
        if args.get("data_path"):
            cmd += ["--data_path", args["data_path"]]

    elif kind == "merge":
        if args.get("merge_all"):
            cmd = [base_python_cmd, str(script), "--merge_all"]
        else:
            mk = args.get("model_key")
            if not mk:
                raise HTTPException(400, "model_key required for merge (or set merge_all)")
            cmd = [base_python_cmd, str(script), "--model_key", mk]
        if args.get("force"):
            cmd.append("--force")

    elif kind == "convert":
        if args.get("convert_all"):
            cmd = [base_python_cmd, str(script), "--convert_all"]
        else:
            mk = args.get("model_key")
            if not mk:
                raise HTTPException(400, "model_key required for convert (or set convert_all)")
            cmd = [base_python_cmd, str(script), "--model_key", mk]
        if args.get("force"):
            cmd.append("--force")

    elif kind in {"convert_hf_exl2", "convert_hf_exl3"}:
        repo_id = str(args.get("repo_id") or "").strip()
        if not repo_id:
            raise HTTPException(400, f"repo_id required for {kind}")
        target_format = _conversion_kind_format(kind) or "exl2"
        default_bits = 6.5 if target_format == "exl2" else 4.5
        bits = _as_positive_float(args.get("bits"), default_bits)
        groupsize_raw = args.get("groupsize")
        groupsize = _as_positive_int(groupsize_raw, 2048) if (target_format == "exl2" or groupsize_raw not in (None, "")) else None

        if target_format == "exl3":
            convert_script, checked = _resolve_exl3_convert_script(args, env)
            if not convert_script:
                raise HTTPException(
                    503,
                    {
                        "message": "EXL3 conversion toolchain unavailable",
                        "required": "Set EXLLAMA_V3_ROOT or EXL3_CONVERT_SCRIPT to an ExLlamaV3 convert.py path",
                        "checked": checked,
                    },
                )

        cmd = [
            base_python_cmd,
            str(script),
            "--repo_id",
            repo_id,
            "--target_format",
            target_format,
            "--bits",
            str(bits),
        ]
        if groupsize is not None:
            cmd += ["--groupsize", str(groupsize)]
        if bool(args.get("force", False)):
            cmd.append("--force")
        if target_format == "exl3":
            cmd += ["--convert_script", str(convert_script)]
        for arg_name, env_name in (
            ("base_models_dir", "BASE_MODELS_DIR"),
            ("webui_models_dir", "WEBUI_MODELS_DIR"),
            ("exllama_root", "EXLLAMA_ROOT"),
        ):
            value = str(args.get(arg_name) or "").strip()
            if value:
                env[env_name] = value
        if target_format == "exl3":
            exllama_v3_root = str(args.get("exllama_root") or "").strip()
            if exllama_v3_root:
                env["EXLLAMA_V3_ROOT"] = exllama_v3_root

    elif kind in {"convert_merged_exl2", "convert_merged_exl3"}:
        model_key = str(args.get("model_key") or "").strip()
        if not model_key:
            raise HTTPException(400, f"model_key required for {kind}")
        target_format = _conversion_kind_format(kind) or "exl2"
        default_bits = 6.5 if target_format == "exl2" else 4.5
        bits = _as_positive_float(args.get("bits"), default_bits)
        groupsize_raw = args.get("groupsize")
        groupsize = _as_positive_int(groupsize_raw, 2048) if (target_format == "exl2" or groupsize_raw not in (None, "")) else None

        if target_format == "exl3":
            convert_script, checked = _resolve_exl3_convert_script(args, env)
            if not convert_script:
                raise HTTPException(
                    503,
                    {
                        "message": "EXL3 conversion toolchain unavailable",
                        "required": "Set EXLLAMA_V3_ROOT or EXL3_CONVERT_SCRIPT to an ExLlamaV3 convert.py path",
                        "checked": checked,
                    },
                )

        cmd = [
            base_python_cmd,
            str(script),
            "--model_key",
            model_key,
            "--target_format",
            target_format,
            "--bits",
            str(bits),
        ]
        if groupsize is not None:
            cmd += ["--groupsize", str(groupsize)]
        if bool(args.get("force", False)):
            cmd.append("--force")
        if target_format == "exl3":
            cmd += ["--convert_script", str(convert_script)]
        source_model_dir = str(args.get("source_model_dir") or "").strip()
        output_dir = str(args.get("output_dir") or "").strip()
        if source_model_dir:
            cmd += ["--source_dir", source_model_dir]
        if output_dir:
            cmd += ["--output_dir", output_dir]
        exllama_root = str(args.get("exllama_root") or "").strip()
        if exllama_root:
            env["EXLLAMA_ROOT"] = exllama_root
            if target_format == "exl3":
                env["EXLLAMA_V3_ROOT"] = exllama_root
    else:
        raise HTTPException(400, "kind must be train|merge|convert|convert_hf_exl2|convert_merged_exl2|convert_hf_exl3|convert_merged_exl3")

    with open(log_path, "w", buffering=1) as lf:
        lf.write(f"### {kind} job {job_id} @ {datetime.now().isoformat()}\n")
        lf.write("$ " + " ".join(cmd) + "\n\n")

    log_handle = open(log_path, "a")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    finally:
        log_handle.close()

    pidfd = None
    if hasattr(os, "pidfd_open"):
        try:
            pidfd = os.pidfd_open(proc.pid, 0)
        except OSError:
            pidfd = None
    process_identity = _process_identity(proc.pid) or {}

    with JOB_LOCK:
        JOB_PROCESSES[job_id] = proc
        if pidfd is not None:
            JOB_PIDFDS[job_id] = pidfd
        JOB_IDENTITIES[job_id] = process_identity
        JOBS[job_id] = {
            "id": job_id, "kind": kind, "args": args, "cmd": cmd,
            "pid": proc.pid, "start_ts": time.time(), "end_ts": None,
            "status": "running", "log": str(log_path),
        }

    if kind in conversion_kinds:
        JOBS[job_id]["conversion"] = _record_conversion_run_start(job_id, kind, args, env, log_path, proc.pid)
    _persist_job(job_id)

    def _watch():
        rc = proc.wait()
        with JOB_LOCK:
            j = JOBS.get(job_id)
            if j:
                j["end_ts"] = time.time()
                j["returncode"] = rc
                j["status"] = "cancelled" if j.get("status") == "cancelling" else ("ok" if rc == 0 else "error")
            JOB_PROCESSES.pop(job_id, None)
            finished_pidfd = JOB_PIDFDS.pop(job_id, None)
            if finished_pidfd is not None:
                os.close(finished_pidfd)
            JOB_IDENTITIES.pop(job_id, None)
        if kind in conversion_kinds:
            _record_conversion_run_finish(job_id, rc)
        _persist_job(job_id)

    threading.Thread(target=_watch, daemon=True).start()
    return JOBS[job_id]

# -----------------------------------------------------------------------------
# API models
# -----------------------------------------------------------------------------
class SwitchReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    mode: str            # chat|intent|small|embed|util
    model_dir: str
    bounce: bool = True
    backend: str | None = None  # tgw|vllm|tabbyapi
    lifecycle_mode: str | None = None  # legacy|auto|native
    native_max_seq_len: int | None = None
    readiness_timeout_seconds: float | None = None


class ModelLoadReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    mode: str            # chat|intent|small|embed|util
    model_dir: str
    bounce: bool = True
    backend: str | None = None  # tgw|vllm|tabbyapi
    lifecycle_mode: str | None = "auto"  # legacy|auto|native
    native_max_seq_len: int | None = None
    readiness_timeout_seconds: float | None = None


class ModelUnloadReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    mode: str            # chat|intent|small|embed|util
    bounce: bool = False
    backend: str | None = None  # tgw|vllm|tabbyapi
    lifecycle_mode: str | None = "auto"  # legacy|auto|native

class Knobs(BaseModel):
    model_config = {"protected_namespaces": ()}
    LLM_CHAT_API_BASE: str | None = None
    LLM_CHAT_API_BASE_TGW: str | None = None
    LLM_CHAT_API_BASE_VLLM: str | None = None
    LLM_CHAT_API_BASE_TABBYAPI: str | None = None
    LLM_INTENT_API_BASE: str | None = None
    LLM_INTENT_API_BASE_TGW: str | None = None
    LLM_INTENT_API_BASE_VLLM: str | None = None
    LLM_INTENT_API_BASE_TABBYAPI: str | None = None
    LLM_SMALL_API_BASE: str | None = None
    LLM_SMALL_API_BASE_TGW: str | None = None
    LLM_SMALL_API_BASE_VLLM: str | None = None
    LLM_SMALL_API_BASE_TABBYAPI: str | None = None
    LLM_EMBED_API_BASE: str | None = None
    LLM_EMBED_API_BASE_TGW: str | None = None
    LLM_EMBED_API_BASE_VLLM: str | None = None
    LLM_EMBED_API_BASE_TABBYAPI: str | None = None
    SMART_ASSISTANT_URL: str | None = None
    CUDA_VISIBLE_DEVICES: str | None = None
    PM2_CHAT: str | None = None
    PM2_INTENT: str | None = None
    PM2_SMALL: str | None = None
    PM2_EMBED: str | None = None
    OPENROUTER_API_BASE: str | None = None
    OPENAI_API_BASE: str | None = None
    DEFAULT_ROUTING_STRATEGY: str | None = None
    DEFAULT_FREE_FALLBACK_ALLOWED: str | None = None
    DEFAULT_PAID_FALLBACK_ALLOWED: str | None = None
    TGW_CHAT_WEBUI_ENABLED: str | None = None
    TGW_CHAT_WEBUI_PORT: str | None = None
    TGW_CHAT_WEBUI_BIND_HOST: str | None = None
    TGW_CHAT_WEBUI_PUBLIC_URL: str | None = None
    TGW_WEBUI_ENABLED: str | None = None
    TGW_WEBUI_PORT: str | None = None
    TGW_WEBUI_BIND_HOST: str | None = None
    TGW_WEBUI_PUBLIC_URL: str | None = None


class TgwWebUiConfigReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    port: int | None = Field(default=None, ge=1, le=65535)
    bind_host: str | None = None
    public_url: str | None = None

class JobStart(BaseModel):
    model_config = {"protected_namespaces": ()}
    kind: str
    model_key: str | None = None
    repo_id: str | None = None
    bits: float | None = None
    groupsize: int | None = None
    force: bool | None = None
    train_all: bool | None = None
    merge_all: bool | None = None
    convert_all: bool | None = None
    data_path: str | None = None
    base_models_dir: str | None = None
    webui_models_dir: str | None = None
    exllama_root: str | None = None


class Exl2ConversionStartReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    source_type: str = "huggingface_repo"
    repo_id: str | None = None
    model_key: str | None = None
    source_model_dir: str | None = None
    output_dir: str | None = None
    bits: float = Field(default=6.5, gt=0)
    groupsize: int = Field(default=2048, gt=0)
    force: bool = False
    base_models_dir: str | None = None
    webui_models_dir: str | None = None
    exllama_root: str | None = None
    convert_script: str | None = None


class Exl3ConversionStartReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    source_type: str = "huggingface_repo"
    repo_id: str | None = None
    model_key: str | None = None
    source_model_dir: str | None = None
    output_dir: str | None = None
    bits: float = Field(default=4.5, gt=0)
    groupsize: int | None = Field(default=None, gt=0)
    force: bool = False
    base_models_dir: str | None = None
    webui_models_dir: str | None = None
    exllama_root: str | None = None
    convert_script: str | None = None


class GovernanceWriteReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    document: dict
    expected_version: str | None = None
    validate_only: bool = False
    reason: str = ""
    actor: str = "webui"


class GovernanceRollbackReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    expected_version: str | None = None
    reason: str
    actor: str = "webui"


class ProviderModelFlagsReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_key: str
    disabled_until_manual_review: bool | None = None
    exclude_from_free_rotation: bool | None = None
    reason: str
    actor: str = "webui"


class CuratedModelUpdateReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    provider: str
    bucket: str
    model_id: str
    enabled: bool | None = None
    priority: int | None = None
    backend: str | None = None
    notes: str | None = None
    expected_version: str | None = None
    reason: str
    actor: str = "webui"


class OpenRouterFreeDiscoveryReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    refresh_catalog: bool = True
    include_rankings: bool = True
    include_curated: bool = False
    auto_smoke_check: bool = False
    smoke_top_n: int = 5
    auto_promote_top_n: int = 2
    smoke_timeout_s: int = 15
    smoke_prompt: str = "Reply with OK only."
    activate_top_n: int = 0
    clear_active_ids: bool = False
    max_candidates: int = 20
    sort_by: str = "score"
    min_context_length: int | None = None
    max_total_params_b: float | None = None
    max_active_params_b: float | None = None
    min_popularity_tokens: float | None = None
    allow_unknown_size: bool = True
    require_tools: bool | None = None
    require_structured_outputs: bool | None = None
    require_reasoning: bool | None = None
    require_vision: bool | None = None
    family_allow: list[str] = Field(default_factory=list)
    family_deny: list[str] = Field(default_factory=list)
    actor: str = "webui"
    reason: str = ""


class PoliciesTestReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    task_type: str = "chat"
    project_id: str | None = None
    provider_preferences: RouterProviderPreferences = Field(default_factory=RouterProviderPreferences)
    metadata: dict = Field(default_factory=dict)


class RouterRouteTestReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    task_type: str = "chat"
    project_id: str | None = None
    system: str | None = None
    prompt: str = "Route test prompt"
    messages: list[dict] = Field(default_factory=lambda: [{"role": "user", "content": "Route test prompt"}])
    max_tokens: int | None = 64
    provider_preferences: RouterProviderPreferences = Field(default_factory=RouterProviderPreferences)
    model_preferences: RouterModelPreferences = Field(default_factory=RouterModelPreferences)
    metadata: dict = Field(default_factory=dict)
    execute_first: bool = False

# -----------------------------------------------------------------------------
# Routes: baseline (restore everything the old UI used)
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    env = read_env()
    provider_models = read_provider_models()
    provider_policies = read_provider_policies()
    chat_engine = _engine_def("chat", env=env, provider_models=provider_models)
    intent_engine = _engine_def("intent", env=env, provider_models=provider_models)
    small_engine = _engine_def("small", env=env, provider_models=provider_models)
    embed_engine = _engine_def("embed", env=env, provider_models=provider_models)
    info = {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "active": current_links(),
        "slot_backends": read_slot_backends(),
        "provider_config": {
            "models_loaded": bool(provider_models),
            "policies_loaded": bool(provider_policies),
        },
        "pm2": {
            "chat": env.get("PM2_CHAT"),
            "intent": env.get("PM2_INTENT"),
            "small": env.get("PM2_SMALL"),
            "embed": env.get("PM2_EMBED"),
        },
        "api_bases": {
            "chat": chat_engine.get("base"),
            "intent": intent_engine.get("base"),
            "small": small_engine.get("base"),
            "embed": embed_engine.get("base"),
        },
        "schema_versions": {
            "config": CONFIG_SCHEMA_VERSION,
            "runtime": _runtime_store().schema_version(),
        },
    }
    # quick non-fatal pings (TGW exposes /v1/models, not /health)
    try:
        r = requests.get(str(chat_engine.get("base", "")).rstrip("/") + "/v1/models", timeout=2)
        info["chat_up"] = (r.status_code == 200)
    except Exception:
        info["chat_up"] = False
    try:
        r = requests.get(str(intent_engine.get("base", "")).rstrip("/") + "/v1/models", timeout=2)
        info["intent_up"] = (r.status_code == 200)
    except Exception:
        info["intent_up"] = False
    try:
        r = requests.get(str(small_engine.get("base", "")).rstrip("/") + "/v1/models", timeout=2)
        info["small_up"] = (r.status_code == 200)
    except Exception:
        info["small_up"] = False
    try:
        r = requests.get(str(embed_engine.get("base", "")).rstrip("/") + "/v1/models", timeout=2)
        info["embed_up"] = (r.status_code == 200)
    except Exception:
        info["embed_up"] = False
    return info

@app.get("/system")
def system():
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1 = load5 = load15 = 0.0
    total, used, free = shutil.disk_usage("/")
    mem_total = mem_free = mem_avail = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1]) * 1024
            if line.startswith("MemFree:"):
                mem_free = int(line.split()[1]) * 1024
            if line.startswith("MemAvailable:"):
                mem_avail = int(line.split()[1]) * 1024
    except Exception:
        pass
    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "loadavg": [load1, load5, load15],
        "disk": {"total": total, "used": used, "free": free},
        "mem": {"total": mem_total, "free": mem_free, "available": mem_avail},
    }

@app.get("/models")
def models():
    chat_list = list_non_intent_models()
    intent_list = list_intent_models()
    small_list = list_non_intent_models()
    active = current_links()
    env = read_env()
    provider_models = read_provider_models()

    # Slot visibility (honour ENABLE_* from .env)
    slots_enabled = {
        "chat":   env.get("ENABLE_CHAT", "1") == "1",
        "intent": env.get("ENABLE_INTENT", "1") == "1",
        "small":  env.get("ENABLE_SMALL", "1") == "1",
        "embed":  env.get("ENABLE_EMBED", "1") == "1",
    }

    # Per-model metadata (kind, loader, bpw)
    all_names = sorted(set(chat_list + intent_list + small_list))
    meta = inspect_batch(all_names)
    embed_list = [
        name
        for name in all_names
        if "embeddings" in set(meta.get(name, {}).get("capabilities", []))
    ]
    slot_endpoints = {
        mode: _local_endpoint_for_model(
            _slot_alias_for_mode(mode),
            env,
            provider_models=provider_models,
            fallback_mode=mode,
        )
        for mode in SLOT_MODES
    }

    return {
        "chat": chat_list,
        "intent": intent_list,
        "small": small_list,
        "embed": embed_list,
        "active": active,
        "slot_backends": read_slot_backends(),
        "slot_endpoints": slot_endpoints,
        "slots_enabled": slots_enabled,
        "meta": meta,
        "converted_artifacts": _conversion_artifact_rows()[:120],
    }

@app.post("/switch")
def switch(req: SwitchReq):
    return switch_model(
        req.mode,
        req.model_dir,
        req.bounce,
        req.backend,
        req.lifecycle_mode,
        req.native_max_seq_len,
        False,
        req.readiness_timeout_seconds,
    )


@app.post("/models/load")
def model_load(req: ModelLoadReq):
    return switch_model(
        req.mode,
        req.model_dir,
        req.bounce,
        req.backend,
        req.lifecycle_mode,
        req.native_max_seq_len,
        True,
        req.readiness_timeout_seconds,
    )


@app.post("/models/unload")
@_model_lifecycle_transactional
def model_unload(req: ModelUnloadReq):
    mode = _normalize_slot_mode(req.mode, default="")
    if mode not in SLOT_MODES:
        raise HTTPException(400, "mode must be chat|intent|small|embed|util")

    if req.backend is not None and req.backend not in SUPPORTED_BACKENDS:
        raise HTTPException(400, f"backend must be one of: {', '.join(sorted(SUPPORTED_BACKENDS))}")

    normalized_lifecycle_mode = _normalize_switch_lifecycle_mode(req.lifecycle_mode)
    slot_backend = str(read_slot_backends().get(mode, DEFAULT_SLOT_BACKENDS.get(mode, "tgw")) or "tgw")
    previous_backend = slot_backend
    if req.backend is not None and req.backend != slot_backend:
        raise HTTPException(
            409,
            f"requested backend '{req.backend}' does not match active slot backend '{slot_backend}'",
        )

    if normalized_lifecycle_mode == "native" and slot_backend != "tabbyapi":
        raise HTTPException(400, "lifecycle_mode=native requires backend=tabbyapi for the selected slot")

    should_try_native = normalized_lifecycle_mode == "native" or (
        normalized_lifecycle_mode == "auto" and slot_backend == "tabbyapi"
    )

    native_unload_attempted = False
    native_unload_used = False
    native_unload: dict | None = None
    stop_result: dict | None = None
    link = {
        "chat": MODELS_DIR / "chat_active_model",
        "intent": MODELS_DIR / "intent_active_model",
        "small": MODELS_DIR / "small_active_model",
        "embed": MODELS_DIR / "embed_active_model",
    }[mode]
    if link.exists() and not link.is_symlink():
        raise HTTPException(409, f"active model path is not a managed symlink: {link}")
    previous_target = _symlink_target(link)
    if previous_target is None:
        raise HTTPException(409, f"no active model is loaded for slot '{mode}'")
    models_root = MODELS_DIR.resolve()
    if previous_target != models_root and not previous_target.is_relative_to(models_root):
        raise HTTPException(409, f"active model symlink points outside the managed models directory: {link}")

    if should_try_native:
        native_unload_attempted = True
        native_unload = _tabbyapi_native_unload(mode)
        if native_unload.get("ok"):
            native_unload_used = True
        elif normalized_lifecycle_mode == "native":
            detail = str(native_unload.get("detail", "unknown error"))
            raise HTTPException(502, f"tabbyapi native unload failed: {detail}")

    if not native_unload_used:
        if not req.bounce:
            detail = "selected backend has no successful native unload operation"
            if isinstance(native_unload, dict):
                detail = str(native_unload.get("detail") or detail)
            raise HTTPException(409, f"model was not unloaded: {detail}; set bounce=true to stop the engine")
        stop_result = _stop_engine(mode)

    try:
        if link.is_symlink() or link.exists():
            link.unlink()
    except Exception as exc:
        recovery = None
        if stop_result is not None and previous_target is not None:
            try:
                _restore_symlink(link, previous_target)
                recovery = _bounce_engine(mode)
            except Exception as recovery_exc:
                recovery = {"ok": False, "error": str(recovery_exc)}
        elif native_unload_used and previous_target is not None:
            try:
                recovery = _tabbyapi_native_load(mode, previous_target)
            except Exception as recovery_exc:
                recovery = {"ok": False, "error": str(recovery_exc)}
        raise HTTPException(500, {
            "message": "backend unloaded but active model link could not be cleared",
            "cause": str(exc),
            "recovery": recovery,
        }) from exc

    return {
        "ok": True,
        "mode": mode,
        "backend": slot_backend,
        "previous_backend": previous_backend,
        "active_model": None,
        "lifecycle_mode": normalized_lifecycle_mode,
        "native_unload_attempted": native_unload_attempted,
        "native_unload_used": native_unload_used,
        "native_unload": native_unload,
        "bounce": bool(req.bounce),
        "bounce_result": stop_result,
        "unload_method": "tabbyapi_native" if native_unload_used else "engine_stop",
        "engine_stop": stop_result,
    }

@app.get("/knobs")
def get_knobs():
    return redacted_env(read_env())

@app.post("/knobs")
def set_knobs(k: Knobs):
    # Only rewrite the project-local file. Never copy shared/global secrets
    # into the project fallback file as a side effect of changing a knob.
    env = _read_env_file(ENV_PATH)
    for k_, v in k.model_dump().items():
        if v is not None:
            env[k_] = v
    write_env(env)
    return redacted_env(read_env())


def _curated_bucket_rows(doc: dict, provider: str, bucket: str, create_missing: bool = False) -> list[dict] | None:
    provider_key = str(provider or "").strip().lower()
    bucket_key = str(bucket or "").strip().lower()

    if provider_key == "local" and bucket_key == "slots":
        local = doc.get("local")
        if not isinstance(local, dict):
            if not create_missing:
                return None
            local = {}
            doc["local"] = local
        slots = local.get("slots")
        if not isinstance(slots, list):
            if not create_missing:
                return None
            slots = []
            local["slots"] = slots
        return slots

    if provider_key == "local" and bucket_key == "converted_models":
        local = doc.get("local")
        if not isinstance(local, dict):
            if not create_missing:
                return None
            local = {}
            doc["local"] = local
        rows = local.get("converted_models")
        if not isinstance(rows, list):
            if not create_missing:
                return None
            rows = []
            local["converted_models"] = rows
        return rows

    if provider_key == "openrouter" and bucket_key in {"free", "paid"}:
        openrouter = doc.get("openrouter")
        if not isinstance(openrouter, dict):
            if not create_missing:
                return None
            openrouter = {}
            doc["openrouter"] = openrouter
        rows = openrouter.get(bucket_key)
        if not isinstance(rows, list):
            if not create_missing:
                return None
            rows = []
            openrouter[bucket_key] = rows
        return rows

    if provider_key == "openai" and bucket_key == "allowed":
        openai = doc.get("openai")
        if not isinstance(openai, dict):
            if not create_missing:
                return None
            openai = {}
            doc["openai"] = openai
        rows = openai.get("allowed")
        if not isinstance(rows, list):
            if not create_missing:
                return None
            rows = []
            openai["allowed"] = rows
        return rows

    return None


def _curated_model_summary_rows(doc: dict, state: dict) -> list[dict]:
    runtime = state.get("provider_model_state", {}) if isinstance(state.get("provider_model_state"), dict) else {}
    rows = []

    def _append(provider: str, bucket: str, source_rows: list[dict]):
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("id", "") or "").strip()
            if not model_id:
                continue
            runtime_key = _provider_model_key(provider, model_id)
            runtime_row = runtime.get(runtime_key, {}) if isinstance(runtime.get(runtime_key), dict) else {}
            rows.append({
                "provider": provider,
                "bucket": bucket,
                "model_id": model_id,
                "label": str(row.get("label") or model_id),
                "enabled": bool(row.get("enabled", True)),
                "priority": row.get("priority"),
                "backend": row.get("backend"),
                "family": row.get("family"),
                "tier": row.get("tier"),
                "format": row.get("format"),
                "max_context": row.get("max_context"),
                "notes": row.get("notes"),
                "runtime": {
                    "promotion_state": runtime_row.get("promotion_state"),
                    "disabled_until_manual_review": bool(runtime_row.get("disabled_until_manual_review", False)),
                    "exclude_from_free_rotation": bool(runtime_row.get("exclude_from_free_rotation", False)),
                    "failure_count_24h": int(runtime_row.get("failure_count_24h", 0) or 0),
                    "failure_count_7d": int(runtime_row.get("failure_count_7d", 0) or 0),
                    "last_error_type": runtime_row.get("last_error_type"),
                },
            })

    _append("local", "slots", _curated_bucket_rows(doc, "local", "slots") or [])
    _append("local", "converted_models", _curated_bucket_rows(doc, "local", "converted_models") or [])
    _append("openrouter", "free", _curated_bucket_rows(doc, "openrouter", "free") or [])
    _append("openrouter", "paid", _curated_bucket_rows(doc, "openrouter", "paid") or [])
    _append("openai", "allowed", _curated_bucket_rows(doc, "openai", "allowed") or [])

    rows.sort(key=lambda item: (
        str(item.get("provider", "")),
        str(item.get("bucket", "")),
        int(item.get("priority", 999999) if isinstance(item.get("priority"), (int, float)) else 999999),
        str(item.get("model_id", "")),
    ))
    return rows


@app.get("/providers/models")
def providers_models():
    doc = read_provider_models()
    return {
        "models": doc,
        "version": _doc_version(doc),
        "source": str(PROVIDER_MODELS_PATH),
        "local_conversion_artifacts": _conversion_artifact_rows()[:200],
        "time": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/providers/models/curated-summary")
def providers_models_curated_summary(
    provider: str | None = Query(None),
    bucket: str | None = Query(None),
    enabled_only: bool = Query(False),
    search: str | None = Query(None),
    limit: int = Query(300, ge=1, le=2000),
):
    doc = read_provider_models()
    state = read_provider_runtime_state()
    rows = _curated_model_summary_rows(doc, state)

    provider_filter = str(provider or "").strip().lower()
    if provider_filter:
        rows = [row for row in rows if str(row.get("provider", "")).lower() == provider_filter]

    bucket_filter = str(bucket or "").strip().lower()
    if bucket_filter:
        rows = [row for row in rows if str(row.get("bucket", "")).lower() == bucket_filter]

    if enabled_only:
        rows = [row for row in rows if bool(row.get("enabled", False))]

    search_q = str(search or "").strip().lower()
    if search_q:
        rows = [
            row for row in rows
            if search_q in str(row.get("model_id", "")).lower()
            or search_q in str(row.get("label", "")).lower()
            or search_q in str(row.get("notes", "")).lower()
        ]

    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "count": min(limit, len(rows)),
        "version": _doc_version(doc),
        "rows": rows[:limit],
    }


@app.post("/providers/models/curated-entry")
@_state_transactional
def providers_models_curated_entry_update(req: CuratedModelUpdateReq):
    reason = str(req.reason or "").strip()
    if not reason:
        raise HTTPException(422, {"errors": ["reason is required"]})

    provider_key = str(req.provider or "").strip().lower()
    bucket_key = str(req.bucket or "").strip().lower()
    model_id = str(req.model_id or "").strip()
    if not provider_key or not bucket_key or not model_id:
        raise HTTPException(422, {"errors": ["provider, bucket, and model_id are required"]})

    if req.backend is not None and provider_key != "local":
        raise HTTPException(422, {"errors": ["backend updates are only supported for local curated entries"]})

    if req.backend is not None and bucket_key == "slots":
        backend_val = str(req.backend or "").strip().lower()
        if backend_val not in SUPPORTED_BACKENDS:
            raise HTTPException(422, {"errors": [f"backend must be one of: {', '.join(sorted(SUPPORTED_BACKENDS))}"]})

    current_doc = read_provider_models()
    new_doc = json.loads(json.dumps(current_doc))
    rows = _curated_bucket_rows(new_doc, provider_key, bucket_key, create_missing=False)
    if rows is None:
        raise HTTPException(422, {"errors": ["unsupported provider/bucket pair"]})

    target = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "") or "").strip() == model_id:
            target = row
            break
    if target is None:
        raise HTTPException(404, {"message": "curated model entry not found", "provider": provider_key, "bucket": bucket_key, "model_id": model_id})

    if req.enabled is not None:
        target["enabled"] = bool(req.enabled)
    if req.priority is not None:
        target["priority"] = int(req.priority)
    if req.backend is not None:
        target["backend"] = str(req.backend)
    if req.notes is not None:
        target["notes"] = str(req.notes)

    result = _governance_apply(
        resource="models",
        target_path=PROVIDER_MODELS_PATH,
        current_doc=current_doc,
        new_doc=new_doc,
        expected_version=req.expected_version,
        actor=str(req.actor or "webui"),
        reason=reason,
        validate_only=False,
    )

    updated_row = next(
        (
            row for row in _curated_model_summary_rows(new_doc, read_provider_runtime_state())
            if str(row.get("provider", "")) == provider_key
            and str(row.get("bucket", "")) == bucket_key
            and str(row.get("model_id", "")) == model_id
        ),
        None,
    )

    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "provider": provider_key,
        "bucket": bucket_key,
        "model_id": model_id,
        "entry": updated_row,
        "governance": result,
    }


@app.get("/providers/policies")
def providers_policies():
    doc = read_provider_policies()
    return {
        "policies": doc,
        "version": _doc_version(doc),
        "source": str(PROVIDER_POLICIES_PATH),
        "time": datetime.utcnow().isoformat() + "Z",
    }


@app.put("/providers/models")
def providers_models_write(req: GovernanceWriteReq):
    current = read_provider_models()
    return _governance_apply(
        resource="models",
        target_path=PROVIDER_MODELS_PATH,
        current_doc=current,
        new_doc=req.document,
        expected_version=req.expected_version,
        actor=str(req.actor or "webui"),
        reason=str(req.reason or ""),
        validate_only=bool(req.validate_only),
    )


@app.post("/providers/models/rollback")
def providers_models_rollback(req: GovernanceRollbackReq):
    current = read_provider_models()
    return _governance_rollback(
        resource="models",
        target_path=PROVIDER_MODELS_PATH,
        current_doc=current,
        expected_version=req.expected_version,
        actor=str(req.actor or "webui"),
        reason=str(req.reason or ""),
    )


@app.put("/providers/policies")
def providers_policies_write(req: GovernanceWriteReq):
    current = read_provider_policies()
    return _governance_apply(
        resource="policies",
        target_path=PROVIDER_POLICIES_PATH,
        current_doc=current,
        new_doc=req.document,
        expected_version=req.expected_version,
        actor=str(req.actor or "webui"),
        reason=str(req.reason or ""),
        validate_only=bool(req.validate_only),
    )


@app.post("/providers/policies/rollback")
def providers_policies_rollback(req: GovernanceRollbackReq):
    current = read_provider_policies()
    return _governance_rollback(
        resource="policies",
        target_path=PROVIDER_POLICIES_PATH,
        current_doc=current,
        expected_version=req.expected_version,
        actor=str(req.actor or "webui"),
        reason=str(req.reason or ""),
    )


@app.post("/providers/policies/test")
def providers_policies_test(req: PoliciesTestReq):
    env = read_env()
    provider_models = read_provider_models()
    policies = read_provider_policies()
    task_type = _normalize_task_type(req.task_type)
    if task_type == "completion":
        probe = RouterCompletionRequest(
            project_id=req.project_id,
            prompt="policy-test",
            provider_preferences=req.provider_preferences,
            model_preferences=RouterModelPreferences(),
            metadata=req.metadata,
        )
    elif task_type == "embed":
        probe = RouterEmbedRequest(
            project_id=req.project_id,
            input="policy-test",
            provider_preferences=req.provider_preferences,
            model_preferences=RouterModelPreferences(),
            metadata=req.metadata,
        )
    else:
        probe = RouterChatRequest(
            project_id=req.project_id,
            messages=[{"role": "user", "content": "policy-test"}],
            provider_preferences=req.provider_preferences,
            model_preferences=RouterModelPreferences(),
            metadata=req.metadata,
        )
    strategy_resolution = _strategy_resolution_context(probe, env, policies)
    strategy = str(strategy_resolution.get("resolved_strategy", "local_first"))
    chain_resolution = _candidate_chain_resolution(
        strategy,
        probe,
        policies,
        provider_models=provider_models,
    )
    chain = list(chain_resolution.get("filtered_chain", [])) if isinstance(chain_resolution.get("filtered_chain", []), list) else []
    if not chain:
        chain = ["local"]
    chain = _constrain_chain_to_preferred_model(probe, chain, provider_models, policies)
    policy_context = _build_route_policy_context(
        probe,
        env,
        policies,
        strategy,
        chain,
        strategy_resolution=strategy_resolution,
        chain_resolution=chain_resolution,
    )
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "task_type": task_type,
        "project_id": _resolve_project_id(probe),
        "strategy": strategy,
        "strategy_resolution": strategy_resolution,
        "chain_resolution": chain_resolution,
        "candidate_chain": chain,
        "policy_context": policy_context,
        "effective_defaults": _effective_policy_defaults(probe, policies),
        "effective_selection": _effective_policy_selection(probe, policies),
        "project_override": _project_override_for_request(probe, policies),
    }


@app.get("/providers/state")
def providers_state():
    return {
        "state": read_provider_runtime_state(),
        "source": str(PROVIDER_STATE_PATH),
        "time": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/providers/retention-state")
def providers_retention_state():
    policies = read_provider_policies()
    state = read_provider_runtime_state()
    retention = _retention_policy_settings(policies)

    request_logs = state.get("request_logs", []) if isinstance(state.get("request_logs"), list) else []
    usage_logs = state.get("usage_logs", []) if isinstance(state.get("usage_logs"), list) else []
    spend_logs = state.get("spend_logs", []) if isinstance(state.get("spend_logs"), list) else []
    governance_audit = state.get("governance_audit", []) if isinstance(state.get("governance_audit"), list) else []
    provider_model_state = state.get("provider_model_state", {}) if isinstance(state.get("provider_model_state"), dict) else {}

    failure_events_total = 0
    promotion_transitions_total = 0
    models_with_failure_events = 0
    models_with_transitions = 0
    for row in provider_model_state.values():
        if not isinstance(row, dict):
            continue
        failure_events = row.get("failure_events", []) if isinstance(row.get("failure_events"), list) else []
        transitions = row.get("promotion_transitions", []) if isinstance(row.get("promotion_transitions"), list) else []
        if failure_events:
            models_with_failure_events += 1
            failure_events_total += len(failure_events)
        if transitions:
            models_with_transitions += 1
            promotion_transitions_total += len(transitions)

    def _first_ts(rows: list[dict]) -> str | None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts = str(row.get("ts", "") or "").strip()
            if ts:
                return ts
        return None

    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "retention": retention,
        "counts": {
            "request_logs": len(request_logs),
            "usage_logs": len(usage_logs),
            "spend_logs": len(spend_logs),
            "governance_audit": len(governance_audit),
            "provider_model_rows": len(provider_model_state),
            "failure_events_total": failure_events_total,
            "promotion_transitions_total": promotion_transitions_total,
            "models_with_failure_events": models_with_failure_events,
            "models_with_transitions": models_with_transitions,
            "oldest_request_ts": _first_ts(request_logs),
            "oldest_usage_ts": _first_ts(usage_logs),
            "oldest_spend_ts": _first_ts(spend_logs),
            "oldest_governance_audit_ts": _first_ts(governance_audit),
        },
    }


@app.post("/providers/state/provider-model-flags")
@_state_transactional
def providers_model_flags_update(req: ProviderModelFlagsReq):
    if not str(req.reason or "").strip():
        raise HTTPException(422, {"errors": ["reason is required"]})
    key = str(req.model_key or "").strip()
    if not key:
        raise HTTPException(422, {"errors": ["model_key is required"]})

    state = read_provider_runtime_state()
    rows = state.get("provider_model_state", {}) if isinstance(state.get("provider_model_state"), dict) else {}
    row = rows.get(key, {}) if isinstance(rows.get(key), dict) else {}

    if req.disabled_until_manual_review is not None:
        row["disabled_until_manual_review"] = bool(req.disabled_until_manual_review)
    if req.exclude_from_free_rotation is not None:
        row["exclude_from_free_rotation"] = bool(req.exclude_from_free_rotation)

    current_state = str(row.get("promotion_state", "") or "").lower()
    if bool(row.get("disabled_until_manual_review", False)) or bool(row.get("exclude_from_free_rotation", False)):
        if current_state != "retired":
            _set_promotion_state_on_row(row, "quarantined", reason="manual_flag_update", actor=req.actor)
    else:
        if current_state == "quarantined":
            _set_promotion_state_on_row(row, "candidate", reason="manual_unquarantine", actor=req.actor)

    row["updated_ts"] = datetime.utcnow().isoformat() + "Z"
    rows[key] = row
    state["provider_model_state"] = rows
    write_provider_runtime_state(state)

    audit_ref = _append_governance_audit({
        "ts": datetime.utcnow().isoformat() + "Z",
        "resource": "provider_model_state",
        "action": "flags_update",
        "actor": str(req.actor or "webui"),
        "reason": str(req.reason or ""),
        "model_key": key,
        "changed_keys": [k for k in ("disabled_until_manual_review", "exclude_from_free_rotation") if getattr(req, k) is not None],
        "outcome": "applied",
    })

    return {
        "ok": True,
        "model_key": key,
        "row": row,
        "audit_ref": audit_ref,
    }


@app.post("/providers/openrouter/refresh")
def providers_openrouter_refresh(include_rankings: bool = Query(False)):
    env = read_env()
    snapshot = _refresh_openrouter_catalog(env, include_rankings=include_rankings)
    return {
        "ok": snapshot.get("error") is None,
        "time": datetime.utcnow().isoformat() + "Z",
        "openrouter_catalog": snapshot,
    }


@app.post("/providers/openrouter/discover-free")
def providers_openrouter_discover_free(req: OpenRouterFreeDiscoveryReq):
    env = read_env()
    provider_models = read_provider_models()
    if req.refresh_catalog:
        _refresh_openrouter_catalog(env, include_rankings=req.include_rankings)
    elif req.include_rankings:
        _refresh_openrouter_rankings_in_cache()
    payload = _build_openrouter_free_candidates(provider_models, req)

    _sync_openrouter_candidate_states(payload, actor=req.actor, reason=req.reason or "discover_free")

    if req.auto_smoke_check:
        smoke = _smoke_check_openrouter_candidates(env, payload, req)
        payload["smoke_check"] = smoke
        promoted_ids = [str(mid) for mid in smoke.get("promoted_ids", []) if str(mid)]
        if promoted_ids:
            existing_active = [str(mid) for mid in payload.get("active_ids", []) if str(mid)]
            merged_active = []
            for model_id in promoted_ids + existing_active:
                if model_id not in merged_active:
                    merged_active.append(model_id)
            payload["active_ids"] = merged_active
            payload["activation_mode"] = "auto_smoke"
            _sync_openrouter_candidate_states(payload, actor=req.actor, reason="auto_smoke_promote")

    _hydrate_openrouter_candidate_runtime_fields(payload)
    _store_manual_openrouter_free_candidates(payload)
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "manual_only": True,
        "free_candidates": payload,
    }


@app.get("/providers/openrouter/free-candidates")
def providers_openrouter_free_candidates():
    state = read_provider_runtime_state()
    cache = state.get("openrouter_catalog_cache", {}) if isinstance(state.get("openrouter_catalog_cache"), dict) else {}
    payload = state.get("openrouter_free_candidates", {}) if isinstance(state.get("openrouter_free_candidates"), dict) else {}
    _hydrate_openrouter_candidate_runtime_fields(payload)
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "manual_only": True,
        "catalog_fetched_ts": cache.get("fetched_ts"),
        "rankings_fetched_ts": cache.get("rankings_fetched_ts"),
        "free_candidates": payload,
    }


@app.get("/providers/openrouter/rate-limit-state")
def providers_openrouter_rate_limit_state():
    state = read_provider_runtime_state()
    limits = state.get("provider_rate_limits", {}) if isinstance(state.get("provider_rate_limits"), dict) else {}
    pool = limits.get("openrouter_free", {}) if isinstance(limits.get("openrouter_free"), dict) else {}
    queue = state.get("provider_request_queue", []) if isinstance(state.get("provider_request_queue"), list) else []
    now = int(time.time())
    window_start = int(pool.get("window_start", 0) or 0)
    window_seconds = int(pool.get("window_seconds", 60) or 60)
    elapsed = max(0, now - window_start) if window_start > 0 else 0
    reset_in_seconds = max(0, window_seconds - elapsed) if window_start > 0 else window_seconds

    by_priority = {}
    for row in queue:
        if not isinstance(row, dict):
            continue
        p = str(row.get("priority", "batch") or "batch")
        by_priority[p] = by_priority.get(p, 0) + 1

    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "rate_limit": {
            "rpm_limit": int(pool.get("rpm_limit", 20) or 20),
            "window_seconds": window_seconds,
            "window_start": window_start,
            "request_count": int(pool.get("request_count", 0) or 0),
            "reset_in_seconds": reset_in_seconds,
        },
        "queue": {
            "depth": len(queue),
            "by_priority": by_priority,
            "items": queue[:100],
        },
    }


@app.get("/router/budget-state")
def router_budget_state():
    policies = read_provider_policies()
    snap = _budget_snapshot(policies)
    budget = policies.get("budget", {}) if isinstance(policies.get("budget"), dict) else {}
    warn_threshold = float(budget.get("warn_threshold_pct", 0.8) or 0.8)

    daily_limit = float(snap.get("daily_limit_usd", 0.0) or 0.0)
    monthly_limit = float(snap.get("monthly_limit_usd", 0.0) or 0.0)
    day_ratio = (float(snap.get("day_total_usd", 0.0)) / daily_limit) if daily_limit > 0 else None
    month_ratio = (float(snap.get("month_total_usd", 0.0)) / monthly_limit) if monthly_limit > 0 else None

    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "budget": snap,
        "warn_threshold_pct": warn_threshold,
        "daily_warn": bool(day_ratio is not None and day_ratio >= warn_threshold),
        "monthly_warn": bool(month_ratio is not None and month_ratio >= warn_threshold),
        "daily_exceeded": bool(day_ratio is not None and day_ratio >= 1.0),
        "monthly_exceeded": bool(month_ratio is not None and month_ratio >= 1.0),
    }


@app.post("/router/route-test")
def router_route_test(req: RouterRouteTestReq):
    env = read_env()
    provider_models = read_provider_models()
    policies = read_provider_policies()

    raw_messages = req.messages if isinstance(req.messages, list) else []
    clean_messages = []
    for row in raw_messages:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role", "user") or "user")
        content = str(row.get("content", "") or "")
        if role in {"system", "user", "assistant", "tool"} and content:
            clean_messages.append({"role": role, "content": content})
    if not clean_messages:
        clean_messages = [{"role": "user", "content": str(req.prompt or "Route test prompt")}]

    task_type = _normalize_task_type(req.task_type)

    if task_type == "completion":
        probe = RouterCompletionRequest(
            project_id=req.project_id,
            prompt=str(req.prompt or "Route test prompt"),
            system=req.system,
            max_tokens=req.max_tokens,
            provider_preferences=req.provider_preferences,
            model_preferences=req.model_preferences,
            metadata=req.metadata,
        )
    elif task_type == "embed":
        probe = RouterEmbedRequest(
            project_id=req.project_id,
            input=str(req.prompt or "Route test prompt"),
            provider_preferences=req.provider_preferences,
            model_preferences=req.model_preferences,
            metadata=req.metadata,
        )
    else:
        probe = RouterChatRequest(
            project_id=req.project_id,
            messages=clean_messages,
            system=req.system,
            max_tokens=req.max_tokens,
            provider_preferences=req.provider_preferences,
            model_preferences=req.model_preferences,
            metadata=req.metadata,
        )

    strategy_resolution = _strategy_resolution_context(probe, env, policies)
    strategy = str(strategy_resolution.get("resolved_strategy", "local_first"))
    chain_resolution = _candidate_chain_resolution(
        strategy,
        probe,
        policies,
        provider_models=provider_models,
    )
    chain = list(chain_resolution.get("filtered_chain", [])) if isinstance(chain_resolution.get("filtered_chain", []), list) else []
    if not chain:
        chain = ["local"]
    chain = _constrain_chain_to_preferred_model(probe, chain, provider_models, policies)
    policy_context = _build_route_policy_context(
        probe,
        env,
        policies,
        strategy,
        chain,
        strategy_resolution=strategy_resolution,
        chain_resolution=chain_resolution,
    )

    attempted_by_lane: dict[str, set[str]] = {}
    candidates = []
    for lane in chain:
        excluded = attempted_by_lane.setdefault(lane, set())
        try:
            provider, model_id = _pick_catalog_model(lane, provider_models, probe, excluded_models=excluded, policies=policies)
        except HTTPException as exc:
            candidates.append({
                "attempt_order": len(candidates) + 1,
                "lane": lane,
                "available": False,
                "error": exc.detail,
            })
            continue
        if model_id in excluded:
            continue
        excluded.add(model_id)
        row = _provider_model_state_row(provider, model_id)
        candidates.append({
            "attempt_order": len(candidates) + 1,
            "lane": lane,
            "provider": provider,
            "model": model_id,
            "in_cooldown": bool(_is_model_in_cooldown(provider, model_id)),
            "promotion_state": row.get("promotion_state"),
            "health_status": _openrouter_health_status(row) if provider == "openrouter" else None,
        })

    execution = None
    if bool(req.execute_first) and candidates:
        first = next((c for c in candidates if not bool(c.get("in_cooldown", False))), candidates[0])
        provider = str(first.get("provider", "local"))
        model = str(first.get("model", ""))
        lane = str(first.get("lane", "local"))
        local_fallback_mode = "embed" if task_type == "embed" else "chat"
        local_selection = (
            _local_endpoint_for_model(model, env, provider_models=provider_models, fallback_mode=local_fallback_mode)
            if provider == "local"
            else None
        )
        try:
            if task_type == "completion":
                completion_req = probe if isinstance(probe, RouterCompletionRequest) else RouterCompletionRequest(
                    project_id=req.project_id,
                    prompt=str(req.prompt or "Route test prompt"),
                    max_tokens=req.max_tokens,
                    provider_preferences=req.provider_preferences,
                    model_preferences=req.model_preferences,
                    metadata=req.metadata,
                )
                payload = _build_completion_payload(completion_req, model, provider)
                raw = _dispatch_provider_completions(provider, env, payload, provider_models=provider_models)
                preview = ""
                if isinstance(raw, dict) and isinstance(raw.get("choices"), list) and raw.get("choices"):
                    first_choice = raw.get("choices", [])[0]
                    if isinstance(first_choice, dict):
                        preview = str(first_choice.get("text", "") or "")[:200]
            elif task_type == "embed":
                embed_req = probe if isinstance(probe, RouterEmbedRequest) else RouterEmbedRequest(
                    project_id=req.project_id,
                    input=str(req.prompt or "Route test prompt"),
                    provider_preferences=req.provider_preferences,
                    model_preferences=req.model_preferences,
                    metadata=req.metadata,
                )
                payload = _build_embed_payload(embed_req, model)
                raw = _dispatch_provider_embeddings(provider, env, payload, provider_models=provider_models)
                count = len(raw.get("data", []) if isinstance(raw, dict) and isinstance(raw.get("data"), list) else [])
                preview = f"embedding_items={count}"
            else:
                chat_req = probe if isinstance(probe, RouterChatRequest) else RouterChatRequest(
                    project_id=req.project_id,
                    messages=clean_messages,
                    system=req.system,
                    max_tokens=req.max_tokens,
                    provider_preferences=req.provider_preferences,
                    model_preferences=req.model_preferences,
                    metadata=req.metadata,
                )
                payload = _build_chat_payload(chat_req, model, provider)
                raw = _dispatch_provider_chat(provider, env, payload, provider_models=provider_models)
                preview = _extract_chat_text(raw)[:200]
            execution = {
                "ok": True,
                "task_type": task_type,
                "lane": lane,
                "provider": provider,
                "model": model,
                "backend": local_selection.get("backend") if isinstance(local_selection, dict) else provider,
                "local_mode": local_selection.get("mode") if isinstance(local_selection, dict) else None,
                "preview": preview,
            }
        except Exception as e:
            execution = {
                "ok": False,
                "task_type": task_type,
                "lane": lane,
                "provider": provider,
                "model": model,
                "backend": local_selection.get("backend") if isinstance(local_selection, dict) else provider,
                "local_mode": local_selection.get("mode") if isinstance(local_selection, dict) else None,
                "error": _normalize_provider_error(provider, e),
            }

    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "task_type": task_type,
        "project_id": _resolve_project_id(probe),
        "strategy": strategy,
        "strategy_resolution": strategy_resolution,
        "chain_resolution": chain_resolution,
        "candidate_chain": chain,
        "policy_context": policy_context,
        "candidates": candidates,
        "execution": execution,
    }


@app.post("/router/chat", response_model=RouterChatResponse)
def router_chat(req: RouterChatRequest):
    env = read_env()
    provider_models = read_provider_models()
    policies = read_provider_policies()
    _maybe_refresh_openrouter_catalog(env)
    strategy_resolution = _strategy_resolution_context(req, env, policies)
    strategy = str(strategy_resolution.get("resolved_strategy", "local_first"))
    effective_defaults = _effective_policy_defaults(req, policies)
    effective_selection = _effective_policy_selection(req, policies)
    chain_resolution = _candidate_chain_resolution(
        strategy,
        req,
        policies,
        effective_defaults=effective_defaults,
        effective_selection=effective_selection,
        provider_models=provider_models,
    )
    chain = list(chain_resolution.get("filtered_chain", [])) if isinstance(chain_resolution.get("filtered_chain", []), list) else []
    if not chain:
        chain = ["local"]
    chain = _constrain_chain_to_preferred_model(req, chain, provider_models, policies)
    allow_fallbacks = _resolve_bool_pref(
        req.provider_preferences.allow_fallbacks,
        bool(effective_defaults.get("allow_fallbacks", True)),
    )

    routing_errors = []
    attempt_trace = []
    selected_provider = None
    selected_model = None
    selected_lane = None
    attempted_by_lane: dict[str, set[str]] = {}
    blocked_providers: dict[str, str] = {}
    raw = None
    for idx, lane in enumerate(chain):
        excluded = attempted_by_lane.setdefault(lane, set())
        try:
            provider, model_id = _pick_catalog_model(lane, provider_models, req, excluded_models=excluded, policies=policies)
        except HTTPException as exc:
            selection_error = {"type": "no_capable_catalog_model", "message": exc.detail, "retryable": False}
            routing_errors.append({"lane": lane, "provider": None, "model": None, "error": selection_error})
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=None,
                model=None,
                result="skipped",
                reason_code="no_capable_catalog_model",
                error=selection_error,
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            continue
        if model_id in excluded:
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="skipped",
                reason_code="duplicate_candidate",
                fallback_action="continue_next_candidate",
            )
            continue
        excluded.add(model_id)

        blocked_reason = blocked_providers.get(str(provider))
        if blocked_reason:
            block_error = {
                "type": "provider_blocked",
                "message": f"provider blocked for this request after {blocked_reason}",
                "retryable": False,
                "blocked_reason": blocked_reason,
            }
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": block_error,
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="skipped",
                reason_code=f"provider_blocked_{_normalized_reason_fragment(blocked_reason)}",
                error=block_error,
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            if idx == len(chain) - 1:
                break
            continue

        if _is_model_in_cooldown(provider, model_id):
            cooldown_error = {
                "type": "cooldown",
                "message": "model is currently in cooldown",
                "retryable": True,
            }
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": cooldown_error,
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="skipped",
                reason_code="cooldown_active",
                error=cooldown_error,
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            if idx == len(chain) - 1:
                break
            continue
        if lane == "openrouter.free":
            allowed, limiter = _consume_openrouter_free_token(policies)
            if not allowed:
                action, overflow_error = _handle_free_tier_overflow(
                    request_class="chat",
                    strategy=strategy,
                    allow_fallbacks=allow_fallbacks,
                    metadata=req.metadata,
                    limiter=limiter,
                    policies=policies,
                )
                if action != "acquired":
                    routing_errors.append({
                        "lane": lane,
                        "provider": provider,
                        "model": model_id,
                        "error": overflow_error,
                    })
                    _append_route_attempt(
                        attempt_trace,
                        lane=lane,
                        provider=provider,
                        model=model_id,
                        result="blocked",
                        reason_code="free_tier_limiter",
                        error=overflow_error,
                        fallback_action=(
                            "break_overflow_policy"
                            if action == "break"
                            else ("break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate")
                        ),
                    )
                    if action == "break" or idx == len(chain) - 1:
                        break
                    continue
        try:
            _enforce_budget_guardrail(
                policies,
                provider,
                lane,
                model_id,
                provider_models,
                req.max_tokens,
            )
        except HTTPException as e:
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": {
                    "type": "budget_blocked",
                    "message": str(e.detail),
                    "retryable": False,
                },
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="blocked",
                reason_code="budget_guardrail",
                error={
                    "type": "budget_blocked",
                    "message": str(e.detail),
                    "retryable": False,
                },
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            if idx == len(chain) - 1:
                break
            continue
        payload = _build_chat_payload(req, model_id, provider)
        try:
            raw = _dispatch_provider_chat(provider, env, payload, provider_models=provider_models)
            selected_provider = provider
            selected_model = model_id
            selected_lane = lane
            _mark_provider_success(provider, model_id)
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="selected",
                reason_code="selected",
                fallback_action="selected",
            )
            break
        except Exception as e:
            normalized = _normalize_provider_error(provider, e)
            _mark_provider_failure(provider, model_id, normalized)
            block_reason = _provider_block_reason(provider, normalized)
            if block_reason:
                blocked_providers[str(provider)] = block_reason
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": normalized,
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="error",
                reason_code=_dispatch_reason_code(normalized),
                error=normalized,
                fallback_action=(
                    "break_fallback_disabled"
                    if not allow_fallbacks
                    else ("break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate")
                ),
            )
            if not allow_fallbacks:
                break
            if idx == len(chain) - 1:
                break

    if raw is None or selected_provider is None or selected_model is None:
        now_ts = datetime.utcnow().isoformat() + "Z"
        failure_policy_context = _build_route_policy_context(
            req,
            env,
            policies,
            strategy,
            chain,
            effective_defaults=effective_defaults,
            effective_selection=effective_selection,
            strategy_resolution=strategy_resolution,
            chain_resolution=chain_resolution,
        )
        failure_decision = {
            "request_id": uuid.uuid4().hex[:12],
            "ts": now_ts,
            "task_type": _normalize_task_type(getattr(req, "task_type", "chat")),
            "project_id": _resolve_project_id(req),
            "strategy": strategy,
            "strategy_source": failure_policy_context.get("strategy_source"),
            "policy_context": failure_policy_context,
            "candidate_chain": chain,
            "selected_lane": None,
            "selected_provider": None,
            "selected_model": None,
            "selected_backend": None,
            "selected_active_local_model": None,
            "outcome": "error",
            "attempt_errors": routing_errors,
            "attempt_trace": attempt_trace,
            "fallback_summary": _build_fallback_summary(
                chain,
                attempt_trace,
                allow_fallbacks,
                None,
                None,
                None,
            ),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "metadata": req.metadata,
        }
        _append_router_decision(failure_decision, policies=policies)
        raise HTTPException(502, {
            "message": "No provider candidate succeeded",
            "strategy": strategy,
            "candidate_chain": chain,
            "errors": routing_errors,
            "attempt_trace": attempt_trace,
            "fallback_summary": failure_decision.get("fallback_summary", {}),
            "request_id": failure_decision.get("request_id"),
        })

    usage_raw = raw.get("usage", {}) if isinstance(raw, dict) else {}
    usage = RouterUsage(
        prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
        total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
    )

    selected_local = (
        _local_endpoint_for_model(selected_model, env, provider_models=provider_models, fallback_mode="chat")
        if selected_provider == "local"
        else None
    )
    selected_backend = selected_local.get("backend") if isinstance(selected_local, dict) else selected_provider
    selected_active = selected_local.get("active_model") if isinstance(selected_local, dict) else None
    policy_context = _build_route_policy_context(
        req,
        env,
        policies,
        strategy,
        chain,
        effective_defaults=effective_defaults,
        effective_selection=effective_selection,
        strategy_resolution=strategy_resolution,
        chain_resolution=chain_resolution,
    )

    choices_raw = raw.get("choices", []) if isinstance(raw, dict) else []
    normalized_choices = []
    for i, ch in enumerate(choices_raw):
        normalized_choices.append(
            RouterChoice(
                index=int(ch.get("index", i)),
                message=ch.get("message", {"role": "assistant", "content": ""}),
                finish_reason=str(ch.get("finish_reason", "stop")),
            )
        )
    if not normalized_choices:
        normalized_choices = [RouterChoice(index=0, message={"role": "assistant", "content": ""}, finish_reason="stop")]

    now_ts = datetime.utcnow().isoformat() + "Z"
    decision = {
        "request_id": uuid.uuid4().hex[:12],
        "ts": now_ts,
        "task_type": _normalize_task_type(getattr(req, "task_type", "chat")),
        "project_id": _resolve_project_id(req),
        "strategy": strategy,
        "strategy_source": policy_context.get("strategy_source"),
        "policy_context": policy_context,
        "candidate_chain": chain,
        "selected_lane": selected_lane,
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "selected_backend": selected_backend,
        "selected_active_local_model": selected_active,
        "outcome": "ok",
        "attempt_errors": routing_errors,
        "attempt_trace": attempt_trace,
        "fallback_summary": _build_fallback_summary(
            chain,
            attempt_trace,
            allow_fallbacks,
            selected_lane,
            selected_provider,
            selected_model,
        ),
        "usage": usage.model_dump(),
        "metadata": req.metadata,
    }

    est_cost = _estimate_request_cost_usd(
        provider_models,
        selected_provider,
        selected_model,
        usage.prompt_tokens,
        usage.completion_tokens,
    )
    decision["estimated_cost_usd"] = est_cost
    _record_spend(
        provider=selected_provider,
        lane=str(selected_lane or ""),
        model=selected_model,
        amount_usd=float(est_cost or 0.0),
        request_id=str(decision["request_id"]),
        strategy=str(strategy),
        policies=policies,
    )
    _append_router_decision(decision, policies=policies)

    return RouterChatResponse(
        id=f"router-{decision['request_id']}",
        created=int(time.time()),
        model=selected_model,
        provider=selected_provider,
        strategy=strategy,
        choices=normalized_choices,
        usage=usage,
        routing=decision,
    )


@app.post("/router/completions", response_model=RouterCompletionResponse)
def router_completions(req: RouterCompletionRequest):
    env = read_env()
    provider_models = read_provider_models()
    policies = read_provider_policies()
    _maybe_refresh_openrouter_catalog(env)
    strategy_resolution = _strategy_resolution_context(req, env, policies)
    strategy = str(strategy_resolution.get("resolved_strategy", "local_first"))
    effective_defaults = _effective_policy_defaults(req, policies)
    effective_selection = _effective_policy_selection(req, policies)
    chain_resolution = _candidate_chain_resolution(
        strategy,
        req,
        policies,
        effective_defaults=effective_defaults,
        effective_selection=effective_selection,
        provider_models=provider_models,
    )
    chain = list(chain_resolution.get("filtered_chain", [])) if isinstance(chain_resolution.get("filtered_chain", []), list) else []
    if not chain:
        chain = ["local"]
    chain = _constrain_chain_to_preferred_model(req, chain, provider_models, policies)
    allow_fallbacks = _resolve_bool_pref(
        req.provider_preferences.allow_fallbacks,
        bool(effective_defaults.get("allow_fallbacks", True)),
    )

    routing_errors = []
    attempt_trace = []
    selected_provider = None
    selected_model = None
    selected_lane = None
    attempted_by_lane: dict[str, set[str]] = {}
    blocked_providers: dict[str, str] = {}
    raw = None
    for idx, lane in enumerate(chain):
        excluded = attempted_by_lane.setdefault(lane, set())
        try:
            provider, model_id = _pick_catalog_model(lane, provider_models, req, excluded_models=excluded, policies=policies)
        except HTTPException as exc:
            selection_error = {"type": "no_capable_catalog_model", "message": exc.detail, "retryable": False}
            routing_errors.append({"lane": lane, "provider": None, "model": None, "error": selection_error})
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=None,
                model=None,
                result="skipped",
                reason_code="no_capable_catalog_model",
                error=selection_error,
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            continue
        if model_id in excluded:
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="skipped",
                reason_code="duplicate_candidate",
                fallback_action="continue_next_candidate",
            )
            continue
        excluded.add(model_id)

        blocked_reason = blocked_providers.get(str(provider))
        if blocked_reason:
            block_error = {
                "type": "provider_blocked",
                "message": f"provider blocked for this request after {blocked_reason}",
                "retryable": False,
                "blocked_reason": blocked_reason,
            }
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": block_error,
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="skipped",
                reason_code=f"provider_blocked_{_normalized_reason_fragment(blocked_reason)}",
                error=block_error,
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            if idx == len(chain) - 1:
                break
            continue

        if _is_model_in_cooldown(provider, model_id):
            cooldown_error = {
                "type": "cooldown",
                "message": "model is currently in cooldown",
                "retryable": True,
            }
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": cooldown_error,
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="skipped",
                reason_code="cooldown_active",
                error=cooldown_error,
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            if idx == len(chain) - 1:
                break
            continue
        if lane == "openrouter.free":
            allowed, limiter = _consume_openrouter_free_token(policies)
            if not allowed:
                action, overflow_error = _handle_free_tier_overflow(
                    request_class="completion",
                    strategy=strategy,
                    allow_fallbacks=allow_fallbacks,
                    metadata=req.metadata,
                    limiter=limiter,
                    policies=policies,
                )
                if action != "acquired":
                    routing_errors.append({
                        "lane": lane,
                        "provider": provider,
                        "model": model_id,
                        "error": overflow_error,
                    })
                    _append_route_attempt(
                        attempt_trace,
                        lane=lane,
                        provider=provider,
                        model=model_id,
                        result="blocked",
                        reason_code="free_tier_limiter",
                        error=overflow_error,
                        fallback_action=(
                            "break_overflow_policy"
                            if action == "break"
                            else ("break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate")
                        ),
                    )
                    if action == "break" or idx == len(chain) - 1:
                        break
                    continue
        try:
            _enforce_budget_guardrail(
                policies,
                provider,
                lane,
                model_id,
                provider_models,
                req.max_tokens,
            )
        except HTTPException as e:
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": {
                    "type": "budget_blocked",
                    "message": str(e.detail),
                    "retryable": False,
                },
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="blocked",
                reason_code="budget_guardrail",
                error={
                    "type": "budget_blocked",
                    "message": str(e.detail),
                    "retryable": False,
                },
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            if idx == len(chain) - 1:
                break
            continue
        payload = _build_completion_payload(req, model_id, provider)
        try:
            raw = _dispatch_provider_completions(provider, env, payload, provider_models=provider_models)
            selected_provider = provider
            selected_model = model_id
            selected_lane = lane
            _mark_provider_success(provider, model_id)
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="selected",
                reason_code="selected",
                fallback_action="selected",
            )
            break
        except Exception as e:
            normalized = _normalize_provider_error(provider, e)
            _mark_provider_failure(provider, model_id, normalized)
            block_reason = _provider_block_reason(provider, normalized)
            if block_reason:
                blocked_providers[str(provider)] = block_reason
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": normalized,
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="error",
                reason_code=_dispatch_reason_code(normalized),
                error=normalized,
                fallback_action=(
                    "break_fallback_disabled"
                    if not allow_fallbacks
                    else ("break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate")
                ),
            )
            if not allow_fallbacks or idx == len(chain) - 1:
                break

    if raw is None or selected_provider is None or selected_model is None:
        now_ts = datetime.utcnow().isoformat() + "Z"
        failure_policy_context = _build_route_policy_context(
            req,
            env,
            policies,
            strategy,
            chain,
            effective_defaults=effective_defaults,
            effective_selection=effective_selection,
            strategy_resolution=strategy_resolution,
            chain_resolution=chain_resolution,
        )
        failure_decision = {
            "request_id": uuid.uuid4().hex[:12],
            "ts": now_ts,
            "task_type": _normalize_task_type(getattr(req, "task_type", "completion")),
            "project_id": _resolve_project_id(req),
            "strategy": strategy,
            "strategy_source": failure_policy_context.get("strategy_source"),
            "policy_context": failure_policy_context,
            "candidate_chain": chain,
            "selected_lane": None,
            "selected_provider": None,
            "selected_model": None,
            "selected_backend": None,
            "selected_active_local_model": None,
            "outcome": "error",
            "attempt_errors": routing_errors,
            "attempt_trace": attempt_trace,
            "fallback_summary": _build_fallback_summary(
                chain,
                attempt_trace,
                allow_fallbacks,
                None,
                None,
                None,
            ),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "metadata": req.metadata,
        }
        _append_router_decision(failure_decision, policies=policies)
        raise HTTPException(502, {
            "message": "No provider candidate succeeded",
            "strategy": strategy,
            "candidate_chain": chain,
            "errors": routing_errors,
            "attempt_trace": attempt_trace,
            "fallback_summary": failure_decision.get("fallback_summary", {}),
            "request_id": failure_decision.get("request_id"),
        })

    usage_raw = raw.get("usage", {}) if isinstance(raw, dict) else {}
    usage = RouterUsage(
        prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
        total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
    )
    choices_raw = raw.get("choices", []) if isinstance(raw, dict) else []
    choices = []
    for i, ch in enumerate(choices_raw):
        choices.append(
            RouterCompletionChoice(
                index=int(ch.get("index", i)),
                text=str(ch.get("text", "")),
                finish_reason=str(ch.get("finish_reason", "stop")),
            )
        )
    if not choices:
        choices = [RouterCompletionChoice(index=0, text="", finish_reason="stop")]

    selected_local = (
        _local_endpoint_for_model(selected_model, env, provider_models=provider_models, fallback_mode="chat")
        if selected_provider == "local"
        else None
    )
    selected_backend = selected_local.get("backend") if isinstance(selected_local, dict) else selected_provider
    selected_active = selected_local.get("active_model") if isinstance(selected_local, dict) else None
    policy_context = _build_route_policy_context(
        req,
        env,
        policies,
        strategy,
        chain,
        effective_defaults=effective_defaults,
        effective_selection=effective_selection,
        strategy_resolution=strategy_resolution,
        chain_resolution=chain_resolution,
    )

    now_ts = datetime.utcnow().isoformat() + "Z"
    decision = {
        "request_id": uuid.uuid4().hex[:12],
        "ts": now_ts,
        "task_type": _normalize_task_type(getattr(req, "task_type", "completion")),
        "project_id": _resolve_project_id(req),
        "strategy": strategy,
        "strategy_source": policy_context.get("strategy_source"),
        "policy_context": policy_context,
        "candidate_chain": chain,
        "selected_lane": selected_lane,
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "selected_backend": selected_backend,
        "selected_active_local_model": selected_active,
        "outcome": "ok",
        "attempt_errors": routing_errors,
        "attempt_trace": attempt_trace,
        "fallback_summary": _build_fallback_summary(
            chain,
            attempt_trace,
            allow_fallbacks,
            selected_lane,
            selected_provider,
            selected_model,
        ),
        "usage": usage.model_dump(),
        "metadata": req.metadata,
    }

    est_cost = _estimate_request_cost_usd(
        provider_models,
        selected_provider,
        selected_model,
        usage.prompt_tokens,
        usage.completion_tokens,
    )
    decision["estimated_cost_usd"] = est_cost
    _record_spend(
        provider=selected_provider,
        lane=str(selected_lane or ""),
        model=selected_model,
        amount_usd=float(est_cost or 0.0),
        request_id=str(decision["request_id"]),
        strategy=str(strategy),
        policies=policies,
    )
    _append_router_decision(decision, policies=policies)

    return RouterCompletionResponse(
        id=f"router-{decision['request_id']}",
        created=int(time.time()),
        model=selected_model,
        provider=selected_provider,
        strategy=strategy,
        choices=choices,
        usage=usage,
        routing=decision,
    )


@app.post("/router/embed", response_model=RouterEmbedResponse)
def router_embed(req: RouterEmbedRequest):
    env = read_env()
    provider_models = read_provider_models()
    policies = read_provider_policies()
    _maybe_refresh_openrouter_catalog(env)
    strategy_resolution = _strategy_resolution_context(req, env, policies)
    strategy = str(strategy_resolution.get("resolved_strategy", "local_first"))
    effective_defaults = _effective_policy_defaults(req, policies)
    effective_selection = _effective_policy_selection(req, policies)
    chain_resolution = _candidate_chain_resolution(
        strategy,
        req,
        policies,
        effective_defaults=effective_defaults,
        effective_selection=effective_selection,
        provider_models=provider_models,
    )
    chain = list(chain_resolution.get("filtered_chain", [])) if isinstance(chain_resolution.get("filtered_chain", []), list) else []
    if not chain:
        chain = ["local"]
    chain = _constrain_chain_to_preferred_model(req, chain, provider_models, policies)
    allow_fallbacks = _resolve_bool_pref(
        req.provider_preferences.allow_fallbacks,
        bool(effective_defaults.get("allow_fallbacks", True)),
    )

    routing_errors = []
    attempt_trace = []
    selected_provider = None
    selected_model = None
    selected_lane = None
    attempted_by_lane: dict[str, set[str]] = {}
    blocked_providers: dict[str, str] = {}
    raw = None
    for idx, lane in enumerate(chain):
        excluded = attempted_by_lane.setdefault(lane, set())
        try:
            provider, model_id = _pick_catalog_model(lane, provider_models, req, excluded_models=excluded, policies=policies)
        except HTTPException as exc:
            selection_error = {"type": "no_capable_catalog_model", "message": exc.detail, "retryable": False}
            routing_errors.append({"lane": lane, "provider": None, "model": None, "error": selection_error})
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=None,
                model=None,
                result="skipped",
                reason_code="no_capable_catalog_model",
                error=selection_error,
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            continue
        if model_id in excluded:
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="skipped",
                reason_code="duplicate_candidate",
                fallback_action="continue_next_candidate",
            )
            continue
        excluded.add(model_id)

        blocked_reason = blocked_providers.get(str(provider))
        if blocked_reason:
            block_error = {
                "type": "provider_blocked",
                "message": f"provider blocked for this request after {blocked_reason}",
                "retryable": False,
                "blocked_reason": blocked_reason,
            }
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": block_error,
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="skipped",
                reason_code=f"provider_blocked_{_normalized_reason_fragment(blocked_reason)}",
                error=block_error,
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            if idx == len(chain) - 1:
                break
            continue

        if _is_model_in_cooldown(provider, model_id):
            cooldown_error = {
                "type": "cooldown",
                "message": "model is currently in cooldown",
                "retryable": True,
            }
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": cooldown_error,
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="skipped",
                reason_code="cooldown_active",
                error=cooldown_error,
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            if idx == len(chain) - 1:
                break
            continue
        if lane == "openrouter.free":
            allowed, limiter = _consume_openrouter_free_token(policies)
            if not allowed:
                action, overflow_error = _handle_free_tier_overflow(
                    request_class="embed",
                    strategy=strategy,
                    allow_fallbacks=allow_fallbacks,
                    metadata=req.metadata,
                    limiter=limiter,
                    policies=policies,
                )
                if action != "acquired":
                    routing_errors.append({
                        "lane": lane,
                        "provider": provider,
                        "model": model_id,
                        "error": overflow_error,
                    })
                    _append_route_attempt(
                        attempt_trace,
                        lane=lane,
                        provider=provider,
                        model=model_id,
                        result="blocked",
                        reason_code="free_tier_limiter",
                        error=overflow_error,
                        fallback_action=(
                            "break_overflow_policy"
                            if action == "break"
                            else ("break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate")
                        ),
                    )
                    if action == "break" or idx == len(chain) - 1:
                        break
                    continue
        try:
            _enforce_budget_guardrail(
                policies,
                provider,
                lane,
                model_id,
                provider_models,
                None,
            )
        except HTTPException as e:
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": {
                    "type": "budget_blocked",
                    "message": str(e.detail),
                    "retryable": False,
                },
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="blocked",
                reason_code="budget_guardrail",
                error={
                    "type": "budget_blocked",
                    "message": str(e.detail),
                    "retryable": False,
                },
                fallback_action="break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate",
            )
            if idx == len(chain) - 1:
                break
            continue
        payload = _build_embed_payload(req, model_id)
        try:
            raw = _dispatch_provider_embeddings(provider, env, payload, provider_models=provider_models)
            selected_provider = provider
            selected_model = model_id
            selected_lane = lane
            _mark_provider_success(provider, model_id)
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="selected",
                reason_code="selected",
                fallback_action="selected",
            )
            break
        except Exception as e:
            normalized = _normalize_provider_error(provider, e)
            _mark_provider_failure(provider, model_id, normalized)
            block_reason = _provider_block_reason(provider, normalized)
            if block_reason:
                blocked_providers[str(provider)] = block_reason
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": normalized,
            })
            _append_route_attempt(
                attempt_trace,
                lane=lane,
                provider=provider,
                model=model_id,
                result="error",
                reason_code=_dispatch_reason_code(normalized),
                error=normalized,
                fallback_action=(
                    "break_fallback_disabled"
                    if not allow_fallbacks
                    else ("break_chain_exhausted" if idx == len(chain) - 1 else "continue_next_candidate")
                ),
            )
            if not allow_fallbacks or idx == len(chain) - 1:
                break

    if raw is None or selected_provider is None or selected_model is None:
        now_ts = datetime.utcnow().isoformat() + "Z"
        failure_policy_context = _build_route_policy_context(
            req,
            env,
            policies,
            strategy,
            chain,
            effective_defaults=effective_defaults,
            effective_selection=effective_selection,
            strategy_resolution=strategy_resolution,
            chain_resolution=chain_resolution,
        )
        failure_decision = {
            "request_id": uuid.uuid4().hex[:12],
            "ts": now_ts,
            "task_type": _normalize_task_type(getattr(req, "task_type", "embed")),
            "project_id": _resolve_project_id(req),
            "strategy": strategy,
            "strategy_source": failure_policy_context.get("strategy_source"),
            "policy_context": failure_policy_context,
            "candidate_chain": chain,
            "selected_lane": None,
            "selected_provider": None,
            "selected_model": None,
            "selected_backend": None,
            "selected_active_local_model": None,
            "outcome": "error",
            "attempt_errors": routing_errors,
            "attempt_trace": attempt_trace,
            "fallback_summary": _build_fallback_summary(
                chain,
                attempt_trace,
                allow_fallbacks,
                None,
                None,
                None,
            ),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "metadata": req.metadata,
        }
        _append_router_decision(failure_decision, policies=policies)
        raise HTTPException(502, {
            "message": "No provider candidate succeeded",
            "strategy": strategy,
            "candidate_chain": chain,
            "errors": routing_errors,
            "attempt_trace": attempt_trace,
            "fallback_summary": failure_decision.get("fallback_summary", {}),
            "request_id": failure_decision.get("request_id"),
        })

    usage_raw = raw.get("usage", {}) if isinstance(raw, dict) else {}
    usage = RouterUsage(
        prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
        completion_tokens=0,
        total_tokens=int(usage_raw.get("total_tokens", usage_raw.get("prompt_tokens", 0) or 0)),
    )

    data_raw = raw.get("data", []) if isinstance(raw, dict) else []
    data = []
    for i, row in enumerate(data_raw):
        emb = row.get("embedding", []) if isinstance(row, dict) else []
        data.append(RouterEmbedDatum(index=int(row.get("index", i)) if isinstance(row, dict) else i, embedding=list(emb)))
    if not data:
        data = [RouterEmbedDatum(index=0, embedding=[])]

    selected_local = (
        _local_endpoint_for_model(selected_model, env, provider_models=provider_models, fallback_mode="embed")
        if selected_provider == "local"
        else None
    )
    selected_backend = selected_local.get("backend") if isinstance(selected_local, dict) else selected_provider
    selected_active = selected_local.get("active_model") if isinstance(selected_local, dict) else None
    policy_context = _build_route_policy_context(
        req,
        env,
        policies,
        strategy,
        chain,
        effective_defaults=effective_defaults,
        effective_selection=effective_selection,
        strategy_resolution=strategy_resolution,
        chain_resolution=chain_resolution,
    )

    now_ts = datetime.utcnow().isoformat() + "Z"
    decision = {
        "request_id": uuid.uuid4().hex[:12],
        "ts": now_ts,
        "task_type": _normalize_task_type(getattr(req, "task_type", "embed")),
        "project_id": _resolve_project_id(req),
        "strategy": strategy,
        "strategy_source": policy_context.get("strategy_source"),
        "policy_context": policy_context,
        "candidate_chain": chain,
        "selected_lane": selected_lane,
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "selected_backend": selected_backend,
        "selected_active_local_model": selected_active,
        "outcome": "ok",
        "attempt_errors": routing_errors,
        "attempt_trace": attempt_trace,
        "fallback_summary": _build_fallback_summary(
            chain,
            attempt_trace,
            allow_fallbacks,
            selected_lane,
            selected_provider,
            selected_model,
        ),
        "usage": usage.model_dump(),
        "metadata": req.metadata,
    }

    est_cost = _estimate_request_cost_usd(
        provider_models,
        selected_provider,
        selected_model,
        usage.prompt_tokens,
        usage.completion_tokens,
    )
    decision["estimated_cost_usd"] = est_cost
    _record_spend(
        provider=selected_provider,
        lane=str(selected_lane or ""),
        model=selected_model,
        amount_usd=float(est_cost or 0.0),
        request_id=str(decision["request_id"]),
        strategy=str(strategy),
        policies=policies,
    )
    _append_router_decision(decision, policies=policies)

    return RouterEmbedResponse(
        id=f"router-{decision['request_id']}",
        created=int(time.time()),
        model=selected_model,
        provider=selected_provider,
        strategy=strategy,
        data=data,
        usage=usage,
        routing=decision,
    )


@app.get("/router/health")
def router_health():
    state = read_provider_runtime_state()
    logs = state.get("request_logs", []) if isinstance(state.get("request_logs"), list) else []
    catalog = state.get("openrouter_catalog_cache", {}) if isinstance(state.get("openrouter_catalog_cache"), dict) else {}
    manual_candidates = state.get("openrouter_free_candidates", {}) if isinstance(state.get("openrouter_free_candidates"), dict) else {}
    budget = _budget_snapshot(read_provider_policies())
    last = logs[-1] if logs else None
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "router": {
            "provider_models_loaded": bool(read_provider_models()),
            "provider_policies_loaded": bool(read_provider_policies()),
            "request_log_count": len(logs),
            "last_request_ts": last.get("ts") if isinstance(last, dict) else None,
            "openrouter_catalog_fetched_ts": catalog.get("fetched_ts"),
            "openrouter_catalog_count": catalog.get("count", 0),
            "openrouter_free_count": len(catalog.get("free_ids", []) if isinstance(catalog.get("free_ids", []), list) else []),
            "openrouter_rankings_fetched_ts": catalog.get("rankings_fetched_ts"),
            "openrouter_manual_candidate_count": len(manual_candidates.get("candidates", []) if isinstance(manual_candidates.get("candidates", []), list) else []),
            "openrouter_manual_active_free_count": len(manual_candidates.get("active_ids", []) if isinstance(manual_candidates.get("active_ids", []), list) else []),
            "budget": budget,
        },
    }


@app.get("/router/last-decisions")
def router_last_decisions(limit: int = Query(20, ge=1, le=200)):
    state = read_provider_runtime_state()
    logs = state.get("request_logs", []) if isinstance(state.get("request_logs"), list) else []
    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "count": min(limit, len(logs)),
        "decisions": logs[-limit:],
    }


def _compact_router_decision(decision: dict) -> dict:
    attempt_errors = decision.get("attempt_errors", []) if isinstance(decision.get("attempt_errors"), list) else []
    attempt_trace = decision.get("attempt_trace", []) if isinstance(decision.get("attempt_trace"), list) else []
    error_types = []
    for row in attempt_errors:
        if not isinstance(row, dict):
            continue
        err = row.get("error", {}) if isinstance(row.get("error"), dict) else {}
        err_type = str(err.get("type", "unknown") or "unknown")
        if err_type not in error_types:
            error_types.append(err_type)

    attempt_reason_codes = []
    for row in attempt_trace:
        if not isinstance(row, dict):
            continue
        reason_code = str(row.get("reason_code", "") or "")
        if reason_code and reason_code not in attempt_reason_codes:
            attempt_reason_codes.append(reason_code)

    policy_context = decision.get("policy_context", {}) if isinstance(decision.get("policy_context"), dict) else {}
    effective_defaults = policy_context.get("effective_defaults", {}) if isinstance(policy_context.get("effective_defaults"), dict) else {}
    fallback_summary = decision.get("fallback_summary", {}) if isinstance(decision.get("fallback_summary"), dict) else {}

    return {
        "request_id": decision.get("request_id"),
        "ts": decision.get("ts"),
        "task_type": decision.get("task_type") or policy_context.get("task_type"),
        "project_id": decision.get("project_id"),
        "strategy": decision.get("strategy"),
        "strategy_source": decision.get("strategy_source") or policy_context.get("strategy_source"),
        "selected_lane": decision.get("selected_lane"),
        "selected_provider": decision.get("selected_provider"),
        "selected_model": decision.get("selected_model"),
        "selected_backend": decision.get("selected_backend"),
        "outcome": decision.get("outcome"),
        "candidate_chain": decision.get("candidate_chain", []),
        "attempt_error_count": len(attempt_errors),
        "attempt_error_types": error_types,
        "attempt_trace_count": len(attempt_trace),
        "attempt_reason_codes": attempt_reason_codes,
        "fallback_summary": fallback_summary,
        "estimated_cost_usd": decision.get("estimated_cost_usd"),
        "usage": decision.get("usage", {}),
        "policy_context": {
            "requested_strategy": policy_context.get("requested_strategy"),
            "resolved_strategy": policy_context.get("resolved_strategy"),
            "strategy_valid": policy_context.get("strategy_valid"),
            "strategy_fallback_applied": policy_context.get("strategy_fallback_applied"),
            "strategy_fallback_reason": policy_context.get("strategy_fallback_reason"),
            "candidate_chain_source": policy_context.get("candidate_chain_source"),
            "service_tier": policy_context.get("service_tier"),
            "service_tier_source": policy_context.get("service_tier_source"),
            "strict_provider_target": policy_context.get("strict_provider_target"),
            "strict_provider_task_allowed_lanes": list(policy_context.get("strict_provider_task_allowed_lanes", [])),
            "strict_provider_task_constraint_applied": bool(policy_context.get("strict_provider_task_constraint_applied", False)),
            "strict_provider_task_constraint_reason": policy_context.get("strict_provider_task_constraint_reason"),
            "strict_provider_filter_relaxed": bool(policy_context.get("strict_provider_filter_relaxed", False)),
            "project_override_applied": bool(policy_context.get("project_override_applied", False)),
            "global_task_override_applied": bool(policy_context.get("global_task_override_applied", False)),
            "project_task_override_applied": bool(policy_context.get("project_task_override_applied", False)),
            "selection_keys": sorted(
                list(policy_context.get("effective_selection", {}).keys())
                if isinstance(policy_context.get("effective_selection"), dict)
                else []
            ),
            "allow_fallbacks": effective_defaults.get("allow_fallbacks"),
            "free_only": effective_defaults.get("free_only"),
            "paid_allowed": effective_defaults.get("paid_allowed"),
            "preferred_provider": effective_defaults.get("preferred_provider"),
        },
    }


@app.get("/router/decision-traces")
def router_decision_traces(
    limit: int = Query(40, ge=1, le=500),
    compact: bool = Query(True),
):
    state = read_provider_runtime_state()
    logs = state.get("request_logs", []) if isinstance(state.get("request_logs"), list) else []
    recent = logs[-limit:]

    by_task_type = {}
    by_backend = {}
    by_provider = {}
    by_strategy = {}
    by_error_type = {}
    by_reason_code = {}
    with_selected_fallback = 0
    for row in recent:
        if not isinstance(row, dict):
            continue
        task_type = str(row.get("task_type", "chat") or "chat")
        backend = str(row.get("selected_backend", "unknown") or "unknown")
        provider = str(row.get("selected_provider", "unknown") or "unknown")
        strategy = str(row.get("strategy", "unknown") or "unknown")
        by_task_type[task_type] = by_task_type.get(task_type, 0) + 1
        by_backend[backend] = by_backend.get(backend, 0) + 1
        by_provider[provider] = by_provider.get(provider, 0) + 1
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1

        fallback_summary = row.get("fallback_summary", {}) if isinstance(row.get("fallback_summary"), dict) else {}
        if bool(fallback_summary.get("used_fallback", False)):
            with_selected_fallback += 1

        attempt_errors = row.get("attempt_errors", []) if isinstance(row.get("attempt_errors"), list) else []
        for attempt in attempt_errors:
            if not isinstance(attempt, dict):
                continue
            err = attempt.get("error", {}) if isinstance(attempt.get("error"), dict) else {}
            err_type = str(err.get("type", "unknown") or "unknown")
            by_error_type[err_type] = by_error_type.get(err_type, 0) + 1

        attempt_trace = row.get("attempt_trace", []) if isinstance(row.get("attempt_trace"), list) else []
        for attempt in attempt_trace:
            if not isinstance(attempt, dict):
                continue
            reason_code = str(attempt.get("reason_code", "") or "")
            if reason_code:
                by_reason_code[reason_code] = by_reason_code.get(reason_code, 0) + 1

    traces = [_compact_router_decision(row) for row in recent if isinstance(row, dict)] if compact else recent
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "count": len(traces),
        "summary": {
            "by_task_type": by_task_type,
            "by_backend": by_backend,
            "by_provider": by_provider,
            "by_strategy": by_strategy,
            "by_error_type": by_error_type,
            "by_reason_code": by_reason_code,
            "with_selected_fallback": with_selected_fallback,
        },
        "traces": traces,
    }


@app.get("/router/usage-summary")
def router_usage_summary(limit: int = Query(200, ge=1, le=2000)):
    state = read_provider_runtime_state()
    logs = state.get("usage_logs", []) if isinstance(state.get("usage_logs"), list) else []
    recent = logs[-limit:]
    by_provider = {}
    spend_total = 0.0
    for row in recent:
        provider = row.get("provider", "unknown")
        item = by_provider.setdefault(provider, {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_total_usd": 0.0,
        })
        item["requests"] += 1
        item["prompt_tokens"] += int(row.get("prompt_tokens", 0) or 0)
        item["completion_tokens"] += int(row.get("completion_tokens", 0) or 0)
        item["total_tokens"] += int(row.get("total_tokens", 0) or 0)
        c = row.get("estimated_cost_usd")
        if c is not None:
            item["estimated_cost_total_usd"] += float(c)
            spend_total += float(c)

    for item in by_provider.values():
        item["estimated_cost_total_usd"] = round(float(item.get("estimated_cost_total_usd", 0.0)), 8)

    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "window_count": len(recent),
        "estimated_cost_total_usd": round(spend_total, 8),
        "by_provider": by_provider,
    }


@app.get("/router/queue-state")
def router_queue_state():
    state = read_provider_runtime_state()
    queue = state.get("provider_request_queue", []) if isinstance(state.get("provider_request_queue"), list) else []
    now_ms = int(time.time() * 1000)
    pending = [q for q in queue if isinstance(q, dict) and q.get("state") == "queued"]
    expired = [q for q in pending if int(q.get("deadline_ms", 0) or 0) < now_ms]
    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "queue_depth": len(queue),
        "pending_count": len(pending),
        "expired_count": len(expired),
        "items": queue[-50:],
    }


@app.get("/router/fallback-stats")
def router_fallback_stats(limit: int = Query(500, ge=1, le=5000)):
    state = read_provider_runtime_state()
    logs = state.get("request_logs", []) if isinstance(state.get("request_logs"), list) else []
    recent = logs[-limit:]

    total = len(recent)
    with_fallback_attempts = 0
    with_attempt_errors = 0
    with_selected_fallback = 0
    by_error_type = {}
    by_reason_code = {}
    by_provider = {}

    for d in recent:
        if not isinstance(d, dict):
            continue
        chain = d.get("candidate_chain", [])
        attempts = d.get("attempt_errors", [])
        attempt_trace = d.get("attempt_trace", []) if isinstance(d.get("attempt_trace"), list) else []
        fallback_summary = d.get("fallback_summary", {}) if isinstance(d.get("fallback_summary"), dict) else {}
        provider = d.get("selected_provider", "unknown")
        by_provider[provider] = by_provider.get(provider, 0) + 1

        if isinstance(chain, list) and len(chain) > 1:
            with_fallback_attempts += 1
        if bool(fallback_summary.get("used_fallback", False)):
            with_selected_fallback += 1
        if isinstance(attempts, list) and attempts:
            with_attempt_errors += 1
            for a in attempts:
                err = a.get("error", {}) if isinstance(a, dict) else {}
                et = err.get("type", "unknown") if isinstance(err, dict) else "unknown"
                by_error_type[et] = by_error_type.get(et, 0) + 1
        for attempt in attempt_trace:
            if not isinstance(attempt, dict):
                continue
            reason_code = str(attempt.get("reason_code", "") or "")
            if reason_code:
                by_reason_code[reason_code] = by_reason_code.get(reason_code, 0) + 1

    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "window_count": total,
        "with_fallback_chain": with_fallback_attempts,
        "with_selected_fallback": with_selected_fallback,
        "with_attempt_errors": with_attempt_errors,
        "by_error_type": by_error_type,
        "by_reason_code": by_reason_code,
        "by_selected_provider": by_provider,
    }


@app.post("/router/evaluate/local", response_model=LocalEvalResponse)
def router_evaluate_local(req: LocalEvalRequest):
    run = _run_local_evaluation(req)
    return LocalEvalResponse(
        run_id=run["run_id"],
        suite_name=run["suite_name"],
        suite_version=run["suite_version"],
        target_mode=run["target_mode"],
        created_ts=run["created_ts"],
        model_count=len(run.get("candidate_models", [])),
        variant_count=len(run.get("variants", [])),
        case_count=len(run.get("cases", [])),
        result_count=len(run.get("results", [])),
        summary=run.get("summary", {}),
        recommendations=run.get("recommendations", []),
    )


@app.post("/router/evaluate/local/async", response_model=LocalEvalEnqueueResponse)
def router_evaluate_local_async(
    req: LocalEvalRequest,
    priority: str = Query("evaluation"),
):
    run_id, queue_position, enqueued_ts = _enqueue_local_evaluation(req, priority)
    return LocalEvalEnqueueResponse(
        run_id=run_id,
        status="queued",
        priority=priority,
        queue_position=queue_position,
        enqueued_ts=enqueued_ts,
    )


@app.get("/router/evaluations/{run_id}")
def router_evaluation_run(run_id: str):
    state = read_provider_runtime_state()
    runs = state.get("evaluation_runs", {}) if isinstance(state.get("evaluation_runs"), dict) else {}
    run = runs.get(run_id)
    if not isinstance(run, dict):
        raise HTTPException(404, "evaluation run not found")
    return run


@app.get("/router/evaluations/{run_id}/report")
def router_evaluation_report(run_id: str):
    state = read_provider_runtime_state()
    reports = state.get("evaluation_reports", {}) if isinstance(state.get("evaluation_reports"), dict) else {}
    report = reports.get(run_id)
    if not isinstance(report, dict):
        raise HTTPException(404, "evaluation report not found")
    return report


@app.get("/router/evaluations")
def router_evaluations_list(
    limit: int = Query(50, ge=1, le=1000),
    status: str | None = Query(None),
    target_mode: str | None = Query(None),
    project: str | None = Query(None),
    model: str | None = Query(None),
    provider: str | None = Query(None),
    lane: str | None = Query(None),
    tag: str | None = Query(None),
    suite_pass: bool | None = Query(None),
    since_ts: str | None = Query(None),
):
    state = read_provider_runtime_state()
    runs = state.get("evaluation_runs", {}) if isinstance(state.get("evaluation_runs"), dict) else {}
    rows = [v for v in runs.values() if isinstance(v, dict)]

    since_dt = _parse_iso_ts(since_ts or "") if since_ts else None
    status_l = status.lower() if status else None
    target_mode_l = target_mode.lower() if target_mode else None
    project_l = project.lower() if project else None
    model_l = model.lower() if model else None
    provider_l = provider.lower() if provider else None
    lane_l = lane.lower() if lane else None
    tag_l = tag.lower() if tag else None

    filtered = []
    for r in rows:
        if status_l and str(r.get("status", "")).lower() != status_l:
            continue
        if target_mode_l and str(r.get("target_mode", "")).lower() != target_mode_l:
            continue
        if project_l:
            metadata = r.get("metadata", {}) if isinstance(r.get("metadata"), dict) else {}
            proj = str(metadata.get("project", "")).lower()
            if proj != project_l:
                continue
        if model_l:
            models = [str(m).lower() for m in r.get("candidate_models", []) if str(m)]
            if model_l not in models:
                continue
        if provider_l:
            result_rows = r.get("results", []) if isinstance(r.get("results"), list) else []
            providers = {str(rr.get("provider", "")).lower() for rr in result_rows if isinstance(rr, dict)}
            if provider_l not in providers:
                continue
        if lane_l:
            result_rows = r.get("results", []) if isinstance(r.get("results"), list) else []
            lanes = {str(rr.get("lane", "")).lower() for rr in result_rows if isinstance(rr, dict)}
            if lane_l not in lanes:
                continue
        if tag_l:
            found = False
            for c in r.get("cases", []):
                if not isinstance(c, dict):
                    continue
                tags = [str(t).lower() for t in c.get("tags", []) if str(t)]
                if tag_l in tags:
                    found = True
                    break
            if not found:
                continue
        if since_dt is not None:
            created = _parse_iso_ts(str(r.get("created_ts", "")))
            if created is None or created < since_dt:
                continue
        if suite_pass is not None and bool(r.get("suite_pass", False)) != bool(suite_pass):
            continue
        filtered.append(r)

    filtered.sort(key=lambda x: str(x.get("created_ts", "")), reverse=True)
    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "count": min(limit, len(filtered)),
        "runs": filtered[:limit],
    }


@app.get("/router/evaluations/{run_id}/compare-compact")
def router_evaluation_compare_compact(run_id: str):
    state = read_provider_runtime_state()
    runs = state.get("evaluation_runs", {}) if isinstance(state.get("evaluation_runs"), dict) else {}
    run = runs.get(run_id)
    if not isinstance(run, dict):
        raise HTTPException(404, "evaluation run not found")
    return _build_compare_compact(run)


@app.get("/router/evaluation-summary")
def router_evaluation_summary(limit: int = Query(20, ge=1, le=200)):
    state = read_provider_runtime_state()
    reports = state.get("evaluation_reports", {}) if isinstance(state.get("evaluation_reports"), dict) else {}
    rows = [v for v in reports.values() if isinstance(v, dict)]
    rows.sort(key=lambda r: str(r.get("created_ts", "")), reverse=True)
    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "count": min(limit, len(rows)),
        "reports": rows[:limit],
    }


@app.get("/router/lane-sufficiency-report")
def router_lane_sufficiency_report(
    limit_groups: int = Query(20, ge=1, le=200),
    limit_runs: int = Query(500, ge=1, le=5000),
    target_mode: str | None = Query(None),
    project: str | None = Query(None),
    since_ts: str | None = Query(None),
    min_rows_per_lane: int = Query(8, ge=1, le=500),
    min_pass_rate: float = Query(0.9, ge=0.0, le=1.0),
    max_pass_gap: float = Query(0.05, ge=0.0, le=1.0),
):
    state = read_provider_runtime_state()
    runs = state.get("evaluation_runs", {}) if isinstance(state.get("evaluation_runs"), dict) else {}
    rows = [v for v in runs.values() if isinstance(v, dict)]
    rows.sort(key=lambda row: str(row.get("created_ts", "")), reverse=True)

    target_mode_l = str(target_mode or "").strip().lower()
    project_l = str(project or "").strip().lower()
    since_dt = _parse_iso_ts(str(since_ts or "")) if since_ts else None

    filtered = []
    for run in rows:
        if str(run.get("status", "")).lower() != "completed":
            continue
        if target_mode_l and str(run.get("target_mode", "")).lower() != target_mode_l:
            continue
        if project_l:
            metadata = run.get("metadata", {}) if isinstance(run.get("metadata"), dict) else {}
            project_value = str(
                metadata.get("project") or metadata.get("project_id") or metadata.get("caller") or ""
            ).strip().lower()
            if project_value != project_l:
                continue
        if since_dt is not None:
            created = _parse_iso_ts(str(run.get("created_ts", "")))
            if created is None or created < since_dt:
                continue
        filtered.append(run)

    limited = filtered[:limit_runs]
    report = _build_lane_sufficiency_report(
        limited,
        min_rows_per_lane=min_rows_per_lane,
        min_pass_rate=min_pass_rate,
        max_pass_gap=max_pass_gap,
        limit_groups=limit_groups,
    )
    report["time"] = datetime.utcnow().isoformat() + "Z"
    report["window_run_count"] = len(limited)
    report["filters"] = {
        "target_mode": target_mode,
        "project": project,
        "since_ts": since_ts,
    }
    return report


@app.get("/router/evaluation-worker-config")
def router_evaluation_worker_config():
    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "worker_count": EVAL_WORKER_COUNT,
        "priority_running_caps": EVAL_PRIORITY_RUNNING_CAPS,
        "worker_tick_seconds": EVAL_WORKER_TICK_SECONDS,
        "max_queue_depth": MAX_EVALUATION_QUEUE,
    }


@app.get("/router/evaluation-queue-state")
def router_evaluation_queue_state(limit: int = Query(100, ge=1, le=500)):
    state = read_provider_runtime_state()
    queue = state.get("evaluation_queue", []) if isinstance(state.get("evaluation_queue"), list) else []
    queued = [q for q in queue if isinstance(q, dict) and q.get("status") == "queued"]
    running = [q for q in queue if isinstance(q, dict) and q.get("status") == "running"]
    completed = [q for q in queue if isinstance(q, dict) and q.get("status") == "completed"]
    error = [q for q in queue if isinstance(q, dict) and q.get("status") == "error"]
    queued.sort(key=lambda x: (_priority_rank(str(x.get("priority", "evaluation"))), str(x.get("enqueued_ts", ""))))
    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "worker_count": EVAL_WORKER_COUNT,
        "priority_running_caps": EVAL_PRIORITY_RUNNING_CAPS,
        "queued_count": len(queued),
        "running_count": len(running),
        "completed_count": len(completed),
        "error_count": len(error),
        "queued": queued[:limit],
        "running": running[:limit],
        "recent_done": (completed + error)[-limit:],
    }


@app.post("/router/evaluation-queue/{run_id}/cancel")
@_state_transactional
def router_evaluation_queue_cancel(run_id: str):
    state = read_provider_runtime_state()
    queue = state.get("evaluation_queue", []) if isinstance(state.get("evaluation_queue"), list) else []
    runs = state.get("evaluation_runs", {}) if isinstance(state.get("evaluation_runs"), dict) else {}
    changed = False
    found = False

    for q in queue:
        if isinstance(q, dict) and q.get("run_id") == run_id:
            found = True
            if q.get("status") == "queued":
                q["status"] = "cancelled"
                q["completed_ts"] = datetime.utcnow().isoformat() + "Z"
                changed = True

    run = runs.get(run_id)
    if isinstance(run, dict) and run.get("status") == "queued":
        run["status"] = "cancelled"
        run["completed_ts"] = datetime.utcnow().isoformat() + "Z"
        runs[run_id] = run
        changed = True

    if not found and run_id not in runs:
        raise HTTPException(404, "evaluation run not found")

    if changed:
        state["evaluation_queue"] = queue
        state["evaluation_runs"] = runs
        write_provider_runtime_state(state)

    return {"ok": True, "run_id": run_id, "changed": changed}


@app.get("/router/evaluation-suites")
def router_evaluation_suites(limit: int = Query(200, ge=1, le=2000)):
    state = read_provider_runtime_state()
    suites = state.get("evaluation_suites", {}) if isinstance(state.get("evaluation_suites"), dict) else {}
    rows = [v for v in suites.values() if isinstance(v, dict)]
    rows.sort(key=lambda r: str(r.get("updated_ts", "")), reverse=True)
    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "count": min(limit, len(rows)),
        "suites": rows[:limit],
    }


@app.get("/router/evaluation-suites/{suite_name}/{suite_version}")
def router_evaluation_suite_get(suite_name: str, suite_version: str):
    state = read_provider_runtime_state()
    suites = state.get("evaluation_suites", {}) if isinstance(state.get("evaluation_suites"), dict) else {}
    key = _eval_suite_key(suite_name, suite_version)
    row = suites.get(key)
    if not isinstance(row, dict):
        raise HTTPException(404, "evaluation suite not found")
    return row


@app.put("/router/evaluation-suites/{suite_name}/{suite_version}")
def router_evaluation_suite_put(suite_name: str, suite_version: str, payload: EvalSuitePayload):
    req = LocalEvalRequest(
        suite_name=suite_name,
        suite_version=suite_version,
        target_mode=payload.target_mode,
        candidate_models=payload.candidate_models,
        variants=payload.variants,
        cases=payload.cases,
        case_pass_threshold_pct=payload.case_pass_threshold_pct,
        suite_pass_threshold_pct=payload.suite_pass_threshold_pct,
        metadata=payload.metadata,
    )
    _upsert_suite_from_request(req)
    return {"ok": True, "suite_name": suite_name, "suite_version": suite_version}


@app.delete("/router/evaluation-suites/{suite_name}/{suite_version}")
@_state_transactional
def router_evaluation_suite_delete(suite_name: str, suite_version: str):
    state = read_provider_runtime_state()
    suites = state.get("evaluation_suites", {}) if isinstance(state.get("evaluation_suites"), dict) else {}
    key = _eval_suite_key(suite_name, suite_version)
    if key not in suites:
        raise HTTPException(404, "evaluation suite not found")
    suites.pop(key, None)
    state["evaluation_suites"] = suites
    write_provider_runtime_state(state)
    return {"ok": True, "suite_name": suite_name, "suite_version": suite_version}


@app.post("/router/evaluation-suites/{suite_name}/{suite_version}/rerun")
def router_evaluation_suite_rerun(suite_name: str, suite_version: str, req: EvalRerunRequest):
    state = read_provider_runtime_state()
    suites = state.get("evaluation_suites", {}) if isinstance(state.get("evaluation_suites"), dict) else {}
    key = _eval_suite_key(suite_name, suite_version)
    suite = suites.get(key)
    if not isinstance(suite, dict):
        raise HTTPException(404, "evaluation suite not found")

    run_req = LocalEvalRequest(
        suite_name=suite_name,
        suite_version=suite_version,
        target_mode=str(suite.get("target_mode", "chat")),
        candidate_models=req.candidate_models or list(suite.get("candidate_models", [])),
        variants=req.variants or list(suite.get("variants", [])),
        cases=list(suite.get("cases", [])),
        case_pass_threshold_pct=req.case_pass_threshold_pct if req.case_pass_threshold_pct is not None else suite.get("case_pass_threshold_pct"),
        suite_pass_threshold_pct=req.suite_pass_threshold_pct if req.suite_pass_threshold_pct is not None else suite.get("suite_pass_threshold_pct"),
        metadata={**dict(suite.get("metadata", {})), **dict(req.metadata), "rerun_of_suite": key},
    )

    if req.async_run:
        run_id, queue_position, enqueued_ts = _enqueue_local_evaluation(run_req, req.priority)
        return {
            "ok": True,
            "mode": "async",
            "run_id": run_id,
            "queue_position": queue_position,
            "enqueued_ts": enqueued_ts,
            "priority": req.priority,
        }

    run = _run_local_evaluation(run_req)
    return {
        "ok": True,
        "mode": "sync",
        "run_id": run.get("run_id"),
        "summary": run.get("summary", {}),
        "recommendations": run.get("recommendations", []),
    }

# Old UI calls /bounce/<mode>. Keep it, but make it systemd-aware.
@app.post("/bounce/{mode}")
def bounce(mode: str):
    if mode not in SLOT_MODES:
        raise HTTPException(400, "mode must be chat|intent|small|embed")
    return _bounce_engine(mode)

# -----------------------------------------------------------------------------
# Model inspection & GPU info
# -----------------------------------------------------------------------------
@app.get("/inspect/{model_name:path}")
def inspect_model(model_name: str):
    """Inspect a model directory: detect format, loader, VRAM estimate, etc."""
    return inspect_one(model_name)

@app.get("/inspect")
def inspect_all_models():
    """Inspect all models in the models directory."""
    all_names = list_non_intent_models() + list_intent_models()
    return inspect_batch(all_names)

@app.get("/vram")
def vram():
    """Current GPU VRAM usage from nvidia-smi."""
    gpus = get_gpu_info()
    if not gpus:
        raise HTTPException(503, "nvidia-smi not available")
    return {"gpus": gpus, "time": datetime.utcnow().isoformat() + "Z"}

# -----------------------------------------------------------------------------
# Test helpers (old UI expected these)
# -----------------------------------------------------------------------------
def _test_openai(base: str, model: str, q: str, max_tokens: int = 32, no_thinking: bool = False):
    base = base.rstrip("/")
    payload = {"model": model, "messages": [{"role": "user", "content": q}], "max_tokens": max_tokens}
    if no_thinking:
        payload["enable_thinking"] = False
    r = requests.post(
        f"{base}/v1/chat/completions",
        json=payload,
        timeout=30
    )
    r.raise_for_status()
    j = r.json()
    text = j.get("choices", [{}])[0].get("message", {}).get("content")
    return {"ok": True, "answer": text, "raw": j}

@app.get("/test-chat")
def test_chat(q: str = "What is the capital of France?", no_thinking: bool = False):
    env = read_env()
    provider_models = read_provider_models()
    base = _engine_def("chat", env=env, provider_models=provider_models).get("base")
    try:
        return _test_openai(str(base or env["LLM_CHAT_API_BASE"]), "chat_active_model", q, max_tokens=64, no_thinking=no_thinking)
    except Exception as e:
        raise HTTPException(502, f"chat api error: {e}")

@app.get("/test-intent")
def test_intent(q: str = "Return ONLY the word OK.", no_thinking: bool = False):
    env = read_env()
    provider_models = read_provider_models()
    base = _engine_def("intent", env=env, provider_models=provider_models).get("base")
    try:
        return _test_openai(str(base or env["LLM_INTENT_API_BASE"]), "intent_active_model", q, max_tokens=32, no_thinking=no_thinking)
    except Exception as e:
        raise HTTPException(502, f"intent api error: {e}")

@app.get("/test-util")
def test_util(q: str = "Say OK and nothing else.", no_thinking: bool = False):
    env = read_env()
    provider_models = read_provider_models()
    base = _engine_def("small", env=env, provider_models=provider_models).get("base")
    try:
        return _test_openai(str(base or env["LLM_SMALL_API_BASE"]), "small_active_model", q, max_tokens=32, no_thinking=no_thinking)
    except Exception as e:
        raise HTTPException(502, f"small api error: {e}")

# -----------------------------------------------------------------------------
# Jobs endpoints (train/merge/convert)
# -----------------------------------------------------------------------------
@app.get("/jobs")
def jobs_list():
    conversion_runs = {}
    try:
        state = read_provider_runtime_state()
        conversion_runs = state.get("conversion_runs", {}) if isinstance(state.get("conversion_runs"), dict) else {}
    except Exception:
        conversion_runs = {}

    out = []
    with JOB_LOCK:
        jobs_snapshot = [dict(j) for j in JOBS.values()]
    for j in jobs_snapshot:
        row = {
            "id": j["id"], "kind": j["kind"], "status": j["status"],
            "start_ts": j["start_ts"], "end_ts": j.get("end_ts"),
            "pid": j["pid"], "returncode": j.get("returncode"),
            "log": j["log"], "args": j["args"]
        }
        conversion = conversion_runs.get(str(j.get("id", "")))
        if isinstance(conversion, dict):
            row["conversion"] = conversion
        out.append(row)
    out.sort(key=lambda x: x["start_ts"], reverse=True)
    return out

@app.get("/jobs/{job_id}")
def jobs_detail(job_id: str, tail: int = 120):
    with JOB_LOCK:
        source = JOBS.get(job_id)
        j = dict(source) if source else None
    if not j:
        persisted = _runtime_store().get_job(job_id)
        if persisted:
            persisted.pop("_process_identity", None)
            j = persisted
        else:
            raise HTTPException(404, "job not found")
    payload = {**j, "tail": _tail(Path(j["log"]), n=tail)}
    conversion = _conversion_run_by_job_id(job_id)
    if isinstance(conversion, dict):
        payload["conversion"] = conversion
    return payload

@app.post("/jobs")
def jobs_start(req: JobStart):
    return _launch_job(req.kind, req.model_dump())

@app.post("/jobs/{job_id}/cancel")
def jobs_cancel(job_id: str):
    duplicated_pidfd = None
    with JOB_LOCK:
        j = JOBS.get(job_id)
        if not j:
            raise HTTPException(404, "job not found")
        proc = JOB_PROCESSES.get(job_id)
        managed_pidfd = JOB_PIDFDS.get(job_id)
        if proc is not None and proc.poll() is not None:
            j["status"] = "ended"
            raise HTTPException(409, "job process has already ended")
        if proc is None and managed_pidfd is None:
            raise HTTPException(409, "job process is not attached to this API instance")
        if managed_pidfd is not None:
            duplicated_pidfd = os.dup(managed_pidfd)
        j["status"] = "cancelling"
    _persist_job(job_id)
    try:
        if duplicated_pidfd is not None and hasattr(signal, "pidfd_send_signal"):
            signal.pidfd_send_signal(duplicated_pidfd, signal.SIGTERM)
        else:
            # The Popen object, not the persisted PID, is the source of identity on
            # platforms without pidfds.
            proc.terminate()
        return {"ok": True, "status": "cancelling"}
    except ProcessLookupError:
        with JOB_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["status"] = "ended"
        raise HTTPException(409, "job process has already ended")
    finally:
        if duplicated_pidfd is not None:
            os.close(duplicated_pidfd)


def _conversion_source_type_alias(value: str) -> str:
    source = str(value or "huggingface_repo").strip().lower()
    if source in {"huggingface_repo", "hf", "repo"}:
        return "huggingface_repo"
    if source in {"merged_local_model", "merged", "local_merged"}:
        return "merged_local_model"
    return source


def _start_managed_conversion(payload: dict, source_type_value: str, target_format: str) -> dict:
    source_type = _conversion_source_type_alias(source_type_value)
    target_format = _normalized_conversion_format(target_format, default="exl2")
    kind = ""

    if source_type == "huggingface_repo":
        repo_id = str(payload.get("repo_id") or "").strip()
        if not repo_id:
            raise HTTPException(422, {"errors": ["repo_id is required when source_type is huggingface_repo"]})
        payload["repo_id"] = _validate_repo_id(repo_id)
        payload["source_type"] = "huggingface_repo"
        kind = f"convert_hf_{target_format}"
    elif source_type == "merged_local_model":
        model_key = str(payload.get("model_key") or "").strip()
        source_model_dir = str(payload.get("source_model_dir") or "").strip()
        if not model_key and source_model_dir:
            source_name = Path(source_model_dir).name
            if source_name.startswith("merged_"):
                model_key = source_name[len("merged_"):]
        if not model_key:
            raise HTTPException(422, {"errors": ["model_key is required when source_type is merged_local_model"]})

        source_path = _resolve_path_within(
            source_model_dir or (ROOT / "output" / f"merged_{model_key}"),
            [ROOT / "output", MODELS_DIR.resolve()],
            must_exist=True,
            must_be_dir=True,
            label="source_model_dir",
        )

        if str(payload.get("output_dir") or "").strip():
            payload["output_dir"] = str(_resolve_path_within(
                str(payload["output_dir"]),
                [ROOT / "output", MODELS_DIR.resolve()],
                label="output_dir",
            ))

        payload["model_key"] = model_key
        payload["source_model_dir"] = str(source_path)
        payload["source_type"] = "merged_local_model"
        kind = f"convert_merged_{target_format}"
    else:
        raise HTTPException(422, {"errors": ["source_type must be huggingface_repo or merged_local_model"]})

    payload["target_format"] = target_format
    job = _launch_job(kind, payload)
    return {
        "ok": True,
        "job": job,
        "source_type": payload.get("source_type"),
        "target_format": target_format,
        "conversion": _conversion_run_by_job_id(str(job.get("id", ""))),
        "time": datetime.utcnow().isoformat() + "Z",
    }


def _conversion_runs_for_format(target_format: str) -> list[dict]:
    state = read_provider_runtime_state()
    runs = state.get("conversion_runs", {}) if isinstance(state.get("conversion_runs"), dict) else {}
    kinds = _conversion_kinds_for_format(target_format)
    rows = [
        row
        for row in runs.values()
        if isinstance(row, dict)
        and str(row.get("kind", "")) in kinds
    ]
    rows.sort(key=lambda row: str(row.get("created_ts", "")), reverse=True)
    return rows


def _conversion_run_detail_for_format(job_id: str, target_format: str, tail: int) -> dict:
    row = _conversion_run_by_job_id(job_id)
    if not isinstance(row, dict):
        raise HTTPException(404, "conversion job not found")
    if str(row.get("kind", "")) not in _conversion_kinds_for_format(target_format):
        raise HTTPException(404, "conversion job not found")

    payload = dict(row)
    log_path = Path(str(payload.get("log") or ""))
    payload["tail"] = _tail(log_path, n=tail) if str(payload.get("log") or "") else []
    return payload


def _conversion_artifact_detail_for_format(artifact_id: str, target_format: str) -> dict:
    rows = _conversion_artifact_rows(format_filter=target_format)
    for row in rows:
        if str(row.get("artifact_id", "")) == artifact_id:
            return row
    raise HTTPException(404, "conversion artifact not found")


@app.post("/conversions/exl2")
def conversions_exl2_start(req: Exl2ConversionStartReq):
    return _start_managed_conversion(req.model_dump(), req.source_type, target_format="exl2")


@app.get("/conversions/exl2/jobs")
def conversions_exl2_jobs(limit: int = Query(200, ge=1, le=1000)):
    rows = _conversion_runs_for_format("exl2")
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "count": min(limit, len(rows)),
        "runs": rows[:limit],
    }


@app.get("/conversions/exl2/jobs/{job_id}")
def conversions_exl2_job_detail(job_id: str, tail: int = Query(120, ge=1, le=1000)):
    payload = _conversion_run_detail_for_format(job_id, "exl2", tail)
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "run": payload,
    }


@app.get("/conversions/exl2/artifacts")
def conversions_exl2_artifacts(limit: int = Query(200, ge=1, le=2000)):
    rows = _conversion_artifact_rows(format_filter="exl2")
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "count": min(limit, len(rows)),
        "artifacts": rows[:limit],
    }


@app.get("/conversions/exl2/artifacts/{artifact_id}")
def conversions_exl2_artifact_detail(artifact_id: str):
    row = _conversion_artifact_detail_for_format(artifact_id, "exl2")
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "artifact": row,
    }


@app.post("/conversions/exl3")
def conversions_exl3_start(req: Exl3ConversionStartReq):
    return _start_managed_conversion(req.model_dump(), req.source_type, target_format="exl3")


@app.get("/conversions/exl3/jobs")
def conversions_exl3_jobs(limit: int = Query(200, ge=1, le=1000)):
    rows = _conversion_runs_for_format("exl3")
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "count": min(limit, len(rows)),
        "runs": rows[:limit],
    }


@app.get("/conversions/exl3/jobs/{job_id}")
def conversions_exl3_job_detail(job_id: str, tail: int = Query(120, ge=1, le=1000)):
    payload = _conversion_run_detail_for_format(job_id, "exl3", tail)
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "run": payload,
    }


@app.get("/conversions/exl3/artifacts")
def conversions_exl3_artifacts(limit: int = Query(200, ge=1, le=2000)):
    rows = _conversion_artifact_rows(format_filter="exl3")
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "count": min(limit, len(rows)),
        "artifacts": rows[:limit],
    }


@app.get("/conversions/exl3/artifacts/{artifact_id}")
def conversions_exl3_artifact_detail(artifact_id: str):
    row = _conversion_artifact_detail_for_format(artifact_id, "exl3")
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "artifact": row,
    }

# -----------------------------------------------------------------------------
# Newer: Engines endpoints for the new Engine Controls UI
# -----------------------------------------------------------------------------
def _engine_def(mode: str, env: dict | None = None, provider_models: dict | None = None) -> dict:
    mode_key = _normalize_slot_mode(mode, default="")
    if mode_key not in SLOT_MODES:
        raise HTTPException(400, "mode must be chat|intent|small|embed")

    env_data = env if isinstance(env, dict) else read_env()
    endpoint = _local_endpoint_for_model(
        _slot_alias_for_mode(mode_key),
        env_data,
        provider_models=provider_models,
        fallback_mode=mode_key,
    )
    unit_map = {
        "chat": os.getenv("SYSTEMD_LLM_A") or "",
        "intent": os.getenv("SYSTEMD_LLM_B") or "",
        "small": os.getenv("SYSTEMD_LLM_C") or "",
        "embed": os.getenv("SYSTEMD_LLM_D") or "",
    }
    return {
        "mode": mode_key,
        "unit": unit_map[mode_key],
        "base": endpoint["base"],
        "port": int(endpoint["port"]),
        "backend": endpoint["backend"],
        "base_source": endpoint["base_source"],
    }

@app.get("/engines/status")
def engines_status(request: Request):
    env = read_env()
    provider_models = read_provider_models()
    chat = _engine_def("chat", env=env, provider_models=provider_models)
    intent = _engine_def("intent", env=env, provider_models=provider_models)
    small = _engine_def("small", env=env, provider_models=provider_models)
    embed = _engine_def("embed", env=env, provider_models=provider_models)

    def pack(e: dict) -> dict:
        unit = e["unit"]
        backend = str(e.get("backend", "tgw") or "tgw")
        port = int(e.get("port", SLOT_DEFAULT_PORTS.get(str(e.get("mode", "chat")), 8500)))
        payload = {
            "unit": unit or "(not set)",
            "backend": backend,
            "base": e["base"],
            "base_source": e.get("base_source"),
            "port": port,
            "listening": _is_listening(port),
            "systemd": _systemctl_show(unit) if unit else {"error": "SYSTEMD unit not configured"},
            "active": current_links().get(e["mode"]),
        }
        return payload

    return {
        "chat": pack(chat),
        "intent": pack(intent),
        "small": pack(small),
        "embed": pack(embed),
        "tgw_webui": _tgw_webui_state(env=env, request=request),
        "time": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/engines/tgw-webui/config")
def engines_tgw_webui_config(req: TgwWebUiConfigReq, request: Request):
    effective_env = read_env()
    local_env = _read_env_file(ENV_PATH)
    current = _tgw_webui_config(effective_env)
    local_env["TGW_WEBUI_PORT"] = str(req.port if req.port is not None else current["port"])

    bind_host = (req.bind_host if req.bind_host is not None else current["bind_host"]).strip() or "127.0.0.1"
    local_env["TGW_WEBUI_BIND_HOST"] = bind_host

    if req.public_url is not None:
        cleaned_public_url = req.public_url.strip()
        if cleaned_public_url:
            local_env["TGW_WEBUI_PUBLIC_URL"] = cleaned_public_url
        else:
            local_env.pop("TGW_WEBUI_PUBLIC_URL", None)

    write_env(local_env)
    current_env = read_env()
    return {
        "ok": True,
        "tgw_webui": _tgw_webui_state(env=current_env, request=request),
    }


@app.get("/engines/tgw-webui/status")
def engines_tgw_webui_status(request: Request):
    env = read_env()
    return {
        "ok": True,
        "tgw_webui": _tgw_webui_state(env=env, request=request),
        "time": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/engines/tgw-webui/{action}")
def engines_tgw_webui_action(action: str, request: Request):
    unit = _tgw_webui_action(action)
    env = read_env()
    return {
        "ok": True,
        "mode": "tgw-webui",
        "action": action,
        "unit": unit,
        "tgw_webui": _tgw_webui_state(env=env, request=request),
    }


@app.get("/engines/tgw-webui/logs")
def engines_tgw_webui_logs(lines: int = 160):
    unit = os.getenv("SYSTEMD_TGW_WEBUI", "llm-tgw-webui.service")
    if not unit:
        raise HTTPException(400, "SYSTEMD unit not configured for TGW WebUI (SYSTEMD_TGW_WEBUI)")
    return {"ok": True, "mode": "tgw-webui", "unit": unit, "tail": _journal_tail(unit, lines=lines)}

@app.post("/engines/{mode}/{action}")
def engines_action(mode: str, action: str):
    e = _engine_def(mode)
    unit = e["unit"]
    if not unit:
        raise HTTPException(400, f"SYSTEMD unit not configured for {mode} (SYSTEMD_LLM_*)")

    if action == "start":
        _systemctl_start(unit)
    elif action == "stop":
        _systemctl_stop(unit)
    elif action == "restart":
        _systemctl_restart(unit)
    else:
        raise HTTPException(400, "action must be start|stop|restart")

    return {"ok": True, "mode": mode, "action": action, "unit": unit}


@app.post("/engines/solo/{mode}")
def engines_solo(mode: str):
    # Stop other units first, then start requested.
    units = {
        "chat": os.getenv("SYSTEMD_LLM_A") or "",
        "intent": os.getenv("SYSTEMD_LLM_B") or "",
        "small": os.getenv("SYSTEMD_LLM_C") or "",
        "embed": os.getenv("SYSTEMD_LLM_D") or "",
    }
    if not units.get(mode):
        raise HTTPException(400, f"SYSTEMD unit not configured for {mode}")

    for m, u in units.items():
        if m != mode and u:
            _systemctl_stop(u)

    _systemctl_start(units[mode])
    return {"ok": True, "solo": mode, "stopped": [m for m in units if m != mode and units[m]], "started": units[mode]}

@app.get("/engines/{mode}/logs")
def engines_logs(mode: str, lines: int = 160):
    e = _engine_def(mode)
    unit = e["unit"]
    if not unit:
        raise HTTPException(400, f"SYSTEMD unit not configured for {mode}")
    return {"ok": True, "mode": mode, "unit": unit, "tail": _journal_tail(unit, lines=lines)}

# -----------------------------------------------------------------------------
def main():
    uvicorn.run(app, host=LLM_MANAGER_HOST, port=LLM_MANAGER_PORT)

if __name__ == "__main__":
    main()
