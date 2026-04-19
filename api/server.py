from __future__ import annotations

import os, json, subprocess, time, uuid, threading, signal, shutil, re, hashlib
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from html.parser import HTMLParser
import uvicorn
import requests

from model_inspector import inspect_one, inspect_batch, get_gpu_info, detect_kind, detect_loader
from router.contracts import (
    RouterChatRequest,
    RouterChatResponse,
    RouterChoice,
    RouterUsage,
    RouterCompletionRequest,
    RouterCompletionResponse,
    RouterCompletionChoice,
    RouterEmbedRequest,
    RouterEmbedResponse,
    RouterEmbedDatum,
)
from providers.local import LocalProviderAdapter
from providers.openrouter import OpenRouterProviderAdapter
from providers.openai import OpenAIProviderAdapter
from evaluation.schemas import (
    LocalEvalRequest,
    LocalEvalResponse,
    EvalVariant,
    LocalEvalEnqueueResponse,
    EvalSuitePayload,
    EvalRerunRequest,
)

# -----------------------------------------------------------------------------
# Paths / config
# -----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = Path(os.getenv("SERVER_MODELS_DIR", os.getenv("MODELS_DIR", "/srv/2bananas/engines/models")))
CONFIG_PATH = ROOT / "model_configs.json"
ENV_PATH = ROOT / "secrets" / ".env"
PROVIDER_MODELS_PATH = ROOT / "config" / "provider_models.json"
PROVIDER_POLICIES_PATH = ROOT / "config" / "provider_policies.json"
STATE_DIR = ROOT / "run" / "state"
SLOT_BACKENDS_PATH = STATE_DIR / "slot_backends.json"
PROVIDER_STATE_PATH = STATE_DIR / "provider_runtime_state.json"
SCRIPTS_DIR = ROOT / "app" / "src" / "llm_manager"
LOGS_DIR = ROOT / "run" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_BACKENDS = {"tgw", "vllm", "tabbyapi"}
DEFAULT_SLOT_BACKENDS = {"chat": "tgw", "intent": "tgw", "small": "tgw"}
SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

DEFAULT_PROVIDER_MODELS = {
    "local": {"slots": []},
    "openrouter": {"free": [], "paid": []},
    "openai": {"allowed": []},
}

DEFAULT_PROVIDER_POLICIES = {
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
    "updated_ts": None,
}

MAX_REQUEST_LOGS = 200
MAX_USAGE_LOGS = 1000
MAX_SPEND_LOGS = 2000
MAX_EVALUATION_RUNS = 200
MAX_EVALUATION_QUEUE = 500

EVAL_PRIORITY_ORDER = {"interactive": 0, "batch": 1, "evaluation": 2}
EVAL_WORKER_TICK_SECONDS = 0.5
EVAL_WORKER_LOCK = threading.Lock()
EVAL_QUEUE_CLAIM_LOCK = threading.Lock()
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
  "SMART_ASSISTANT_URL": os.getenv("SMART_ASSISTANT_URL", "http://127.0.0.1:8100/command"),
  "CUDA_VISIBLE_DEVICES":os.getenv("CUDA_VISIBLE_DEVICES","0"),
  "PM2_CHAT":   os.getenv("PM2_CHAT",   "llm_chat"),
  "PM2_INTENT": os.getenv("PM2_INTENT", "llm_lora_intent"),
  "PM2_SMALL":  os.getenv("PM2_SMALL",  "llm_small"),
    "TGW_CHAT_WEBUI_ENABLED": os.getenv("TGW_CHAT_WEBUI_ENABLED", "0"),
    "TGW_CHAT_WEBUI_PORT": os.getenv("TGW_CHAT_WEBUI_PORT", "7860"),
    "TGW_CHAT_WEBUI_BIND_HOST": os.getenv("TGW_CHAT_WEBUI_BIND_HOST", "127.0.0.1"),
    "TGW_CHAT_WEBUI_PUBLIC_URL": os.getenv("TGW_CHAT_WEBUI_PUBLIC_URL", ""),
}

# -----------------------------------------------------------------------------
# Bind knobs (systemd-friendly)
# -----------------------------------------------------------------------------
LLM_MANAGER_HOST = os.getenv("LLM_MANAGER_HOST", "127.0.0.1")
LLM_MANAGER_PORT = int(os.getenv("LLM_MANAGER_PORT", os.getenv("PORT", "8101")))

app = FastAPI(title="LLM Manager API", version="1.2")
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
def read_env() -> dict:
    env = DEFAULTS.copy()
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if not line.strip() or line.strip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def write_env(env: dict):
    lines = [f"{k}={v}" for k, v in env.items()]
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_PATH, 0o600)


def redacted_env(env: dict) -> dict:
    out = {}
    for k, v in env.items():
        if any(marker in k.upper() for marker in SENSITIVE_ENV_MARKERS):
            out[k] = "***REDACTED***" if v else ""
        else:
            out[k] = v
    return out


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


def _chat_tgw_webui_config(env: dict | None = None) -> dict:
    source = env or read_env()
    return {
        "enabled": _env_flag(source.get("TGW_CHAT_WEBUI_ENABLED"), False),
        "port": _env_int(source.get("TGW_CHAT_WEBUI_PORT"), 7860),
        "bind_host": str(source.get("TGW_CHAT_WEBUI_BIND_HOST") or "127.0.0.1").strip() or "127.0.0.1",
        "public_url": str(source.get("TGW_CHAT_WEBUI_PUBLIC_URL") or "").strip(),
    }


def _chat_tgw_webui_launch_url(config: dict, request: Request | None = None) -> str:
    explicit = str(config.get("public_url") or "").strip()
    if explicit:
        return explicit
    host = _request_host_for_port(request)
    if host:
        return f"http://{host}:{config['port']}/"
    return f"http://127.0.0.1:{config['port']}/"


def _chat_tgw_webui_state(
    env: dict | None = None,
    request: Request | None = None,
    backend: str = "tgw",
) -> dict:
    config = _chat_tgw_webui_config(env)
    available = backend == "tgw"
    effective_enabled = bool(available and config["enabled"])
    return {
        "available": available,
        "enabled": config["enabled"],
        "effective_enabled": effective_enabled,
        "port": config["port"],
        "bind_host": config["bind_host"],
        "public_url": config["public_url"],
        "launch_url": _chat_tgw_webui_launch_url(config, request=request),
        "listening": _is_listening(config["port"]) if effective_enabled else False,
        "requires_restart": True,
        "detail": "" if available else f"Chat backend is '{backend}'; TGW WebUI is only available on the tgw lane.",
    }

def list_intent_models():
    if not MODELS_DIR.exists():
        return []
    return sorted([p.name for p in MODELS_DIR.iterdir() if p.is_dir() and p.name.startswith("lora_")])

def list_non_intent_models():
    if not MODELS_DIR.exists():
        return []
    ignore = {"intent_active_model","chat_active_model","small_active_model"}
    return sorted([
        p.name for p in MODELS_DIR.iterdir()
        if p.is_dir() and p.name not in ignore and not p.name.startswith("lora_")
    ])

def _make_symlink(link: Path, target: Path):
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target, target_is_directory=True)


def read_slot_backends() -> dict:
    data = DEFAULT_SLOT_BACKENDS.copy()
    if SLOT_BACKENDS_PATH.exists():
        try:
            raw = json.loads(SLOT_BACKENDS_PATH.read_text())
            if isinstance(raw, dict):
                for mode in ("chat", "intent", "small"):
                    backend = raw.get(mode)
                    if backend in SUPPORTED_BACKENDS:
                        data[mode] = backend
        except Exception:
            pass
    return data


def write_slot_backends(data: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SLOT_BACKENDS_PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def set_slot_backend(mode: str, backend: str):
    current = read_slot_backends()
    current[mode] = backend
    write_slot_backends(current)


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


def read_provider_models() -> dict:
    return _load_json(PROVIDER_MODELS_PATH, DEFAULT_PROVIDER_MODELS)


def read_provider_policies() -> dict:
    return _load_json(PROVIDER_POLICIES_PATH, DEFAULT_PROVIDER_POLICIES)


def read_provider_runtime_state() -> dict:
    state = _load_json(PROVIDER_STATE_PATH, DEFAULT_PROVIDER_RUNTIME_STATE)
    changed = _ensure_provider_runtime_state(state)
    if not PROVIDER_STATE_PATH.exists() or changed:
        write_provider_runtime_state(state)
    return state


def write_provider_runtime_state(state: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["updated_ts"] = datetime.utcnow().isoformat() + "Z"
    PROVIDER_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


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

    if errors:
        raise HTTPException(422, {"errors": errors})


def _validate_provider_policies_document(doc: dict):
    if not isinstance(doc, dict):
        raise HTTPException(422, {"errors": ["provider policies document must be a JSON object"]})

    errors = []
    for root in ("defaults", "openrouter", "budget", "selection"):
        if root not in doc:
            errors.append(f"missing root key '{root}'")
        elif not isinstance(doc.get(root), dict):
            errors.append(f"root key '{root}' must be an object")

    defaults = doc.get("defaults", {}) if isinstance(doc.get("defaults"), dict) else {}
    if "strategy" not in defaults:
        errors.append("defaults.strategy is required")

    selection = doc.get("selection", {}) if isinstance(doc.get("selection"), dict) else {}
    if defaults.get("strategy") and defaults.get("strategy") not in selection:
        errors.append("defaults.strategy must exist in selection map")

    budget = doc.get("budget", {}) if isinstance(doc.get("budget"), dict) else {}
    providers = budget.get("providers", {}) if isinstance(budget.get("providers"), dict) else None
    if providers is None:
        errors.append("budget.providers must be an object")

    if errors:
        raise HTTPException(422, {"errors": errors})


def _append_governance_audit(entry: dict):
    state = read_provider_runtime_state()
    rows = state.get("governance_audit", []) if isinstance(state.get("governance_audit"), list) else []
    rows.append(entry)
    state["governance_audit"] = rows[-MAX_GOVERNANCE_AUDIT:]
    write_provider_runtime_state(state)
    return {
        "ts": entry.get("ts"),
        "resource": entry.get("resource"),
        "action": entry.get("action"),
    }


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


def _governance_rollback(resource: str, target_path: Path, current_doc: dict, expected_version: str | None, actor: str, reason: str):
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


def _append_router_decision(decision: dict):
    state = read_provider_runtime_state()
    logs = state.get("request_logs", [])
    if not isinstance(logs, list):
        logs = []
    logs.append(decision)
    state["request_logs"] = logs[-MAX_REQUEST_LOGS:]

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
    state["usage_logs"] = usage[-MAX_USAGE_LOGS:]
    write_provider_runtime_state(state)


def _provider_model_state_row(provider: str, model: str) -> dict:
    state = read_provider_runtime_state()
    model_state = state.get("provider_model_state", {})
    if not isinstance(model_state, dict):
        return {}
    row = model_state.get(_provider_model_key(provider, model), {})
    return row if isinstance(row, dict) else {}


def _provider_model_key(provider: str, model: str) -> str:
    return f"{provider}:{model}"


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


def _mark_provider_success(provider: str, model: str):
    state = read_provider_runtime_state()
    model_state = state.get("provider_model_state", {})
    if not isinstance(model_state, dict):
        model_state = {}
    key = _provider_model_key(provider, model)
    row = model_state.get(key, {})
    if not isinstance(row, dict):
        row = {}
    row["last_success_ts"] = datetime.utcnow().isoformat() + "Z"
    row["failure_count"] = 0
    row["last_error_type"] = None
    row["disabled_until_manual_review"] = False
    row["exclude_from_free_rotation"] = False
    row["cooldown_until"] = 0
    model_state[key] = row
    state["provider_model_state"] = model_state
    write_provider_runtime_state(state)


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
    err_type = str(normalized_error.get("type", "provider_error"))
    retryable = bool(normalized_error.get("retryable", False))

    cooldown_seconds = 0
    if err_type == "rate_limited":
        cooldown_seconds = 30
    elif err_type in ("quota_exhausted", "not_free_anymore"):
        cooldown_seconds = 300
    elif retryable:
        cooldown_seconds = 10

    row["failure_count"] = failures
    row["last_failure_ts"] = datetime.utcnow().isoformat() + "Z"
    row["last_error_type"] = err_type
    row["last_error_message"] = str(normalized_error.get("message", ""))
    row["cooldown_until"] = time.time() + cooldown_seconds if cooldown_seconds else 0
    if err_type in ("auth_error", "model_unavailable") and failures >= 3:
        row["disabled_until_manual_review"] = True
    if err_type in ("not_free_anymore", "quota_exhausted"):
        row["exclude_from_free_rotation"] = True
    model_state[key] = row
    state["provider_model_state"] = model_state
    write_provider_runtime_state(state)


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


def _enqueue_free_tier_request(request_class: str, strategy: str, metadata: dict, policies: dict) -> tuple[bool, dict]:
    state = read_provider_runtime_state()
    queue = state.get("provider_request_queue", [])
    if not isinstance(queue, list):
        queue = []

    cfg = policies.get("openrouter", {}) if isinstance(policies.get("openrouter", {}), dict) else {}
    max_depth = int(cfg.get("max_queue_depth", 100) or 100)
    max_wait_ms = int(cfg.get("max_queue_wait_ms", 15000) or 15000)
    now_ms = int(time.time() * 1000)

    if len(queue) >= max_depth:
        return False, {
            "max_queue_depth": max_depth,
            "queue_depth": len(queue),
            "max_queue_wait_ms": max_wait_ms,
        }

    entry = {
        "id": uuid.uuid4().hex[:12],
        "request_class": request_class,
        "strategy": strategy,
        "enqueue_ts": datetime.utcnow().isoformat() + "Z",
        "deadline_ms": now_ms + max_wait_ms,
        "state": "queued",
        "metadata": metadata or {},
    }
    queue.append(entry)
    state["provider_request_queue"] = queue
    write_provider_runtime_state(state)
    return True, {
        "entry_id": entry["id"],
        "queue_depth": len(queue),
        "max_queue_depth": max_depth,
        "max_queue_wait_ms": max_wait_ms,
    }


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

    base_error = {
        "type": "protective_rate_limited",
        "message": "OpenRouter free-tier local limiter engaged",
        "retryable": True,
        "limiter": limiter,
        "queue_behavior": behavior,
    }

    if behavior == "wait":
        ok, q = _enqueue_free_tier_request(request_class, strategy, metadata, policies)
        if ok:
            base_error["type"] = "queued"
            base_error["message"] = "Request queued for free-tier capacity"
            base_error["queue"] = q
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


def _resolve_strategy(req: RouterChatRequest, env: dict, policies: dict) -> str:
    req_strategy = req.provider_preferences.strategy
    if req_strategy and req_strategy != "default":
        return req_strategy
    env_strategy = env.get("DEFAULT_ROUTING_STRATEGY", "").strip()
    if env_strategy:
        return env_strategy
    return str(policies.get("defaults", {}).get("strategy", "local_first"))


def _resolve_bool_pref(req_val: bool | None, default_val: bool) -> bool:
    if req_val is None:
        return default_val
    return bool(req_val)


def _candidate_chain_for_strategy(strategy: str, req: RouterChatRequest, policies: dict) -> list[str]:
    if strategy == "strict_provider":
        provider = req.provider_preferences.preferred_provider or "local"
        if provider == "openrouter":
            return ["openrouter.free", "openrouter.paid"]
        return [provider]

    chain = list(policies.get("selection", {}).get(strategy, ["local"]))

    defaults = policies.get("defaults", {})
    free_only = _resolve_bool_pref(req.provider_preferences.free_only, bool(defaults.get("free_only", False)))
    paid_allowed = _resolve_bool_pref(req.provider_preferences.paid_allowed, bool(defaults.get("paid_allowed", True)))

    filtered = []
    for lane in chain:
        if free_only and lane in ("openrouter.paid", "openai"):
            continue
        if not paid_allowed and lane in ("openrouter.paid", "openai"):
            continue
        filtered.append(lane)
    return filtered or ["local"]


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


def _record_spend(provider: str, lane: str, model: str, amount_usd: float, request_id: str, strategy: str):
    if amount_usd is None:
        return
    amount = float(amount_usd)
    if amount <= 0:
        return

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
    if len(daily) > 60:
        for k in sorted(daily.keys())[:-60]:
            daily.pop(k, None)
    if len(monthly) > 24:
        for k in sorted(monthly.keys())[:-24]:
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
    state["spend_logs"] = spend_logs[-MAX_SPEND_LOGS:]
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


def _pick_catalog_model(
    lane: str,
    provider_models: dict,
    req: RouterChatRequest,
    excluded_models: set[str] | None = None,
    policies: dict | None = None,
) -> tuple[str, str]:
    excluded_models = excluded_models or set()
    policies = policies or {}
    preferred = (req.model_preferences.preferred_model or "").strip()
    if preferred:
        if lane.startswith("openrouter"):
            return "openrouter", preferred
        if lane == "openai":
            return "openai", preferred
        return "local", preferred

    if lane == "local":
        return "local", "chat_active_model"

    if lane == "openrouter.free":
        items = provider_models.get("openrouter", {}).get("free", [])
        enabled = [m for m in items if isinstance(m, dict) and m.get("enabled", True)]
        if bool((policies.get("openrouter", {}) if isinstance(policies.get("openrouter", {}), dict) else {}).get("enforce_upstream_free_status", True)):
            upstream_free = _openrouter_upstream_free_ids()
            if upstream_free:
                enabled = [m for m in enabled if str(m.get("id", "")) in upstream_free]
        enabled = [m for m in enabled if str(m.get("id", "")) not in excluded_models]
        enabled = [
            m for m in enabled
            if not bool(_provider_model_state_row("openrouter", str(m.get("id", ""))).get("exclude_from_free_rotation", False))
        ]
        enabled.sort(key=lambda m: int(m.get("priority", 9999)))
        if enabled:
            return "openrouter", str(enabled[0]["id"])

        manual_section = _manual_openrouter_free_candidates_state()
        active_ids = [str(mid) for mid in manual_section.get("active_ids", []) if str(mid)]
        catalog_state = read_provider_runtime_state()
        catalog = catalog_state.get("openrouter_catalog_cache", {}) if isinstance(catalog_state.get("openrouter_catalog_cache"), dict) else {}
        catalog_map = _openrouter_catalog_model_map(catalog)
        upstream_free = _openrouter_upstream_free_ids()
        for model_id in active_ids:
            if model_id in excluded_models:
                continue
            row = catalog_map.get(model_id, {}) if isinstance(catalog_map.get(model_id, {}), dict) else {}
            if not bool(row.get("is_free", False)):
                continue
            if upstream_free and model_id not in upstream_free:
                continue
            model_state = _provider_model_state_row("openrouter", model_id)
            if bool(model_state.get("exclude_from_free_rotation", False)):
                continue
            if bool(model_state.get("disabled_until_manual_review", False)):
                continue
            return "openrouter", model_id

        return "openrouter", "openrouter-free-default"

    if lane == "openrouter.paid":
        items = provider_models.get("openrouter", {}).get("paid", [])
        enabled = [m for m in items if isinstance(m, dict) and m.get("enabled", True)]
        enabled = [m for m in enabled if str(m.get("id", "")) not in excluded_models]
        enabled.sort(key=lambda m: int(m.get("priority", 9999)))
        picked = enabled[0]["id"] if enabled else "openrouter-paid-default"
        return "openrouter", str(picked)

    if lane == "openai":
        items = provider_models.get("openai", {}).get("allowed", [])
        enabled = [m for m in items if isinstance(m, dict) and m.get("enabled", True)]
        enabled = [m for m in enabled if str(m.get("id", "")) not in excluded_models]
        enabled.sort(key=lambda m: int(m.get("priority", 9999)))
        picked = enabled[0]["id"] if enabled else "openai-default"
        return "openai", str(picked)

    return "local", "chat_active_model"


def _build_chat_payload(req: RouterChatRequest, selected_model: str) -> dict:
    messages = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.extend([m.dict() for m in req.messages])
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
            "json_schema": req.json_schema,
        }
    return payload


def _build_completion_payload(req: RouterCompletionRequest, selected_model: str) -> dict:
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
    return payload


def _build_embed_payload(req: RouterEmbedRequest, selected_model: str) -> dict:
    return {
        "model": selected_model,
        "input": req.input,
    }


def _dispatch_provider_chat(provider: str, env: dict, payload: dict) -> dict:
    if provider == "local":
        adapter = LocalProviderAdapter()
        return adapter.chat({"base": env.get("LLM_CHAT_API_BASE", "http://127.0.0.1:8500"), "payload": payload})
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


def _dispatch_provider_completions(provider: str, env: dict, payload: dict) -> dict:
    if provider == "local":
        adapter = LocalProviderAdapter()
        return adapter.completions({"base": env.get("LLM_CHAT_API_BASE", "http://127.0.0.1:8500"), "payload": payload})
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


def _dispatch_provider_embeddings(provider: str, env: dict, payload: dict) -> dict:
    if provider == "local":
        adapter = LocalProviderAdapter()
        return adapter.embeddings({"base": env.get("LLM_CHAT_API_BASE", "http://127.0.0.1:8500"), "payload": payload})
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
        entries = []
        started = False
        index = 0
        while index < len(lines):
            line = lines[index]
            if started and line == "Show more":
                break
            if (
                line.isdigit()
                and index + 6 < len(lines)
                and lines[index + 1] == "."
                and lines[index + 3].lower() == "by"
                and lines[index + 6].lower() == "tokens"
            ):
                tokens_value = _parse_compact_number(lines[index + 5])
                if tokens_value is not None:
                    started = True
                    entries.append({
                        "popularity_rank": int(line),
                        "name": lines[index + 2],
                        "author": lines[index + 4],
                        "popularity_tokens": int(tokens_value),
                    })
                    index += 7
                    while index < len(lines) and not (
                        lines[index].isdigit()
                        and index + 1 < len(lines)
                        and lines[index + 1] == "."
                    ) and lines[index] != "Show more":
                        index += 1
                    continue
            index += 1
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
    if bool(row.get("disabled_until_manual_review", False)):
        return "manual_review"
    if bool(row.get("exclude_from_free_rotation", False)):
        return "quarantined"
    if float(row.get("cooldown_until", 0) or 0) > time.time():
        return "cooldown"
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
        "supports_tools": "tools" in supported_parameters,
        "supports_structured_outputs": "structured_outputs" in supported_parameters,
        "supports_reasoning": "reasoning" in supported_parameters,
        "supports_vision": any(modality in {"image", "video"} for modality in input_modalities),
        "supports_text": not output_modalities or "text" in output_modalities,
        "is_free": is_free,
        "free_detection_source": free_detection_source,
        "popularity_rank": ranking_row.get("popularity_rank") if isinstance(ranking_row, dict) else None,
        "popularity_tokens": ranking_row.get("popularity_tokens") if isinstance(ranking_row, dict) else None,
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


def _store_manual_openrouter_free_candidates(payload: dict):
    state = read_provider_runtime_state()
    state["openrouter_free_candidates"] = payload
    write_provider_runtime_state(state)


def _refresh_openrouter_rankings_in_cache() -> dict:
    state = read_provider_runtime_state()
    cache = state.get("openrouter_catalog_cache", {}) if isinstance(state.get("openrouter_catalog_cache"), dict) else {}
    snapshot = _fetch_openrouter_rankings()
    rankings = snapshot.get("rankings", []) if isinstance(snapshot.get("rankings"), list) else []
    cache["rankings"] = rankings
    cache["rankings_fetched_ts"] = snapshot.get("fetched_ts")
    cache["rankings_error"] = snapshot.get("error")
    cache["models"] = _merge_openrouter_rankings_into_models(cache.get("models", []), rankings)
    state["openrouter_catalog_cache"] = cache
    write_provider_runtime_state(state)
    return cache


def _score_openrouter_candidate(row: dict) -> float:
    score = 0.0
    popularity_rank = row.get("popularity_rank")
    popularity_tokens = row.get("popularity_tokens")
    context_length = float(row.get("context_length") or 0)
    if popularity_rank is not None:
        score += max(0.0, 120.0 - (float(popularity_rank) * 8.0))
    if popularity_tokens is not None:
        score += min(40.0, float(popularity_tokens) / 250_000_000_000.0)
    score += min(20.0, context_length / 65536.0)
    if row.get("supports_tools"):
        score += 12.0
    if row.get("supports_structured_outputs"):
        score += 8.0
    if row.get("supports_reasoning"):
        score += 5.0
    health_status = str(row.get("health_status", "unknown"))
    if health_status == "healthy":
        score += 10.0
    elif health_status in {"manual_review", "quarantined"}:
        score -= 100.0
    elif health_status == "cooldown":
        score -= 25.0
    else:
        score -= float(row.get("failure_count", 0) or 0) * 5.0
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
        if discovery_req.require_structured_outputs is True and not bool(row.get("supports_structured_outputs", False)):
            skipped["capabilities"] += 1
            continue
        if discovery_req.require_reasoning is True and not bool(row.get("supports_reasoning", False)):
            skipped["capabilities"] += 1
            continue
        if discovery_req.require_vision is True and not bool(row.get("supports_vision", False)):
            skipped["capabilities"] += 1
            continue

        model_state = _provider_model_state_row("openrouter", model_id)
        candidate = dict(row)
        candidate["failure_count"] = int(model_state.get("failure_count", 0) or 0)
        candidate["last_success_ts"] = model_state.get("last_success_ts")
        candidate["last_failure_ts"] = model_state.get("last_failure_ts")
        candidate["last_error_type"] = model_state.get("last_error_type")
        candidate["health_status"] = _openrouter_health_status(model_state)
        candidate["activation_eligible"] = not bool(model_state.get("disabled_until_manual_review", False)) and not bool(model_state.get("exclude_from_free_rotation", False))
        candidate["score"] = _score_openrouter_candidate(candidate)
        candidates.append(candidate)

    sort_by = str(discovery_req.sort_by or "score")
    if sort_by == "popularity":
        candidates.sort(key=lambda row: ((row.get("popularity_rank") is None), row.get("popularity_rank") or 999999, -(float(row.get("context_length", 0) or 0))))
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
        existing = state.get("openrouter_free_candidates", {}) if isinstance(state.get("openrouter_free_candidates"), dict) else {}
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
        r = requests.get(f"{base}/models", headers=headers, timeout=20)
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

    state["openrouter_catalog_cache"] = fetched
    write_provider_runtime_state(state)
    return fetched


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


def _eval_base_for_mode(mode: str, env: dict) -> str:
    if mode == "chat":
        return env.get("LLM_CHAT_API_BASE", "http://127.0.0.1:8500")
    if mode == "intent":
        return env.get("LLM_INTENT_API_BASE", "http://127.0.0.1:8501")
    return env.get("LLM_SMALL_API_BASE", "http://127.0.0.1:8502")


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
        if len(parts) == 2 and parts[0] in ("chat", "intent", "small"):
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
        "variants": [v.dict() for v in req.variants],
        "cases": [c.dict() for c in req.cases],
        "case_pass_threshold_pct": req.case_pass_threshold_pct,
        "suite_pass_threshold_pct": req.suite_pass_threshold_pct,
        "metadata": req.metadata,
        "latest_run_id": suites.get(key, {}).get("latest_run_id"),
        "case_count": len(req.cases),
        "variant_count": len(req.variants),
        "updated_ts": datetime.utcnow().isoformat() + "Z",
    }
    state["evaluation_suites"] = suites
    write_provider_runtime_state(state)


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


def _eval_worker_once() -> bool:
    job = _claim_next_eval_job()
    if not isinstance(job, dict):
        return False

    run_id = str(job.get("run_id", ""))
    if not run_id:
        return False

    payload = job.get("request", {}) if isinstance(job.get("request"), dict) else {}
    req = _suite_to_request(payload)
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
        state = read_provider_runtime_state()
        runs = state.get("evaluation_runs", {}) if isinstance(state.get("evaluation_runs"), dict) else {}
        row = runs.get(run_id, {}) if isinstance(runs.get(run_id), dict) else {}
        row["status"] = "error"
        row["error"] = str(e)
        row["completed_ts"] = datetime.utcnow().isoformat() + "Z"
        runs[run_id] = row
        state["evaluation_runs"] = runs
        write_provider_runtime_state(state)
        final_status = "error"

    state = read_provider_runtime_state()
    queue = state.get("evaluation_queue", []) if isinstance(state.get("evaluation_queue"), list) else []
    for q in queue:
        if isinstance(q, dict) and q.get("run_id") == run_id:
            q["status"] = final_status
            q["completed_ts"] = datetime.utcnow().isoformat() + "Z"
    state["evaluation_queue"] = queue
    write_provider_runtime_state(state)
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
        "request": req.dict(),
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
        "variants": [v.dict() for v in req.variants],
        "cases": [c.dict() for c in req.cases],
        "case_pass_threshold_pct": req.case_pass_threshold_pct,
        "suite_pass_threshold_pct": req.suite_pass_threshold_pct,
        "metadata": req.metadata,
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
        local_base = _eval_base_for_mode(eval_mode, env).rstrip("/")
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
                        raw = _dispatch_provider_chat(provider, env, payload)
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
                    plugin_results, plugin_score, plugin_score_max = _run_scoring_plugins(text, case.dict())
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
        "variants": [v.dict() for v in variants],
        "cases": [c.dict() for c in req.cases],
        "case_pass_threshold_pct": req.case_pass_threshold_pct,
        "suite_pass_threshold_pct": req.suite_pass_threshold_pct,
        "suite_pass": suite_pass,
        "metadata": req.metadata,
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

    suite_key = f"{req.suite_name}:{req.suite_version}"
    state = read_provider_runtime_state()
    runs = state.get("evaluation_runs", {})
    if not isinstance(runs, dict):
        runs = {}
    runs[run_id] = run_record

    if len(runs) > MAX_EVALUATION_RUNS:
        ordered = sorted(
            runs.items(),
            key=lambda kv: str(kv[1].get("created_ts", "")),
        )
        for old_id, _ in ordered[:-MAX_EVALUATION_RUNS]:
            runs.pop(old_id, None)

    reports = state.get("evaluation_reports", {})
    if not isinstance(reports, dict):
        reports = {}
    reports[run_id] = {
        "run_id": run_id,
        "suite_name": req.suite_name,
        "suite_version": req.suite_version,
        "created_ts": created_ts,
        "suite_pass": suite_pass,
        "summary": summary,
        "recommendations": recommendations,
    }

    suites = state.get("evaluation_suites", {})
    if not isinstance(suites, dict):
        suites = {}
    prior_suite = suites.get(suite_key, {}) if isinstance(suites.get(suite_key), dict) else {}
    suites[suite_key] = {
        "suite_name": req.suite_name,
        "suite_version": req.suite_version,
        "target_mode": req.target_mode,
        "candidate_specs": candidate_specs,
        "candidate_models": candidate_models,
        "variants": [v.dict() for v in variants],
        "cases": [c.dict() for c in req.cases],
        "case_pass_threshold_pct": req.case_pass_threshold_pct,
        "suite_pass_threshold_pct": req.suite_pass_threshold_pct,
        "latest_run_id": run_id,
        "case_count": len(req.cases),
        "variant_count": len(variants),
        "updated_ts": datetime.utcnow().isoformat() + "Z",
        "metadata": req.metadata,
        "created_ts": prior_suite.get("created_ts", datetime.utcnow().isoformat() + "Z"),
    }

    state["evaluation_runs"] = runs
    state["evaluation_reports"] = reports
    state["evaluation_suites"] = suites
    write_provider_runtime_state(state)

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
    return {
        "chat":   tgt(ck) if ck.exists() else None,
        "intent": tgt(ik) if ik.exists() else None,
        "small":  tgt(sk) if sk.exists() else None,
    }

# -----------------------------------------------------------------------------
# Helpers: systemd + pm2
# -----------------------------------------------------------------------------
def _pm2(cmd: list[str]):
    try:
        subprocess.run(["pm2"] + cmd, check=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"pm2 error: {e}")

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
    unit = {"chat": a, "intent": b, "small": c}.get(mode)

    if unit:
        _systemctl_restart(unit)
        return {"ok": True, "method": "systemd", "unit": unit}

    # Legacy fallback
    env = read_env()
    pm2name = {"chat": env.get("PM2_CHAT"), "intent": env.get("PM2_INTENT"), "small": env.get("PM2_SMALL")}.get(mode)
    if pm2name:
        _pm2(["restart", pm2name])
        return {"ok": True, "method": "pm2", "proc": pm2name}

    return {"ok": False, "method": "none", "detail": "No SYSTEMD_LLM_* unit and no PM2_* proc configured"}

# -----------------------------------------------------------------------------
# Core: model switch
# -----------------------------------------------------------------------------
def switch_model(mode: str, model_dir: str, bounce: bool, backend: str | None = None):
    # Historical UI used 'util' for the third slot -> treat as small.
    if mode == "util":
        mode = "small"
    if mode not in ("chat", "intent", "small"):
        raise HTTPException(400, "mode must be chat|intent|small|util")

    if backend is not None and backend not in SUPPORTED_BACKENDS:
        raise HTTPException(400, f"backend must be one of: {', '.join(sorted(SUPPORTED_BACKENDS))}")

    target = MODELS_DIR / model_dir
    if not target.exists():
        raise HTTPException(404, f"model dir not found: {target}")

    link = {
        "chat":   MODELS_DIR / "chat_active_model",
        "intent": MODELS_DIR / "intent_active_model",
        "small":  MODELS_DIR / "small_active_model",
    }[mode]

    _make_symlink(link, target)
    if backend is not None:
        set_slot_backend(mode, backend)
    if bounce:
        _bounce_engine(mode)

    slot_backend = read_slot_backends().get(mode, "tgw")
    return {
        "ok": True,
        "link": str(link),
        "target": str(target),
        "mode": mode,
        "backend": slot_backend,
    }

# -----------------------------------------------------------------------------
# Jobs: tiny runner for train/merge/convert
# -----------------------------------------------------------------------------
JOBS: dict[str, dict] = {}

def _tail(path: Path, n: int = 120) -> list[str]:
    if not path.exists():
        return []
    try:
        out = subprocess.check_output(["tail", "-n", str(n), str(path)], stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return path.read_text(errors="replace").splitlines()[-n:]

def _launch_job(kind: str, args: dict) -> dict:
    job_id = uuid.uuid4().hex[:12]
    log_path = LOGS_DIR / f"{int(time.time())}_{kind}_{job_id}.log"

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = read_env().get("CUDA_VISIBLE_DEVICES", "0")

    script_map = {
        "train":   SCRIPTS_DIR / "train_lora.py",
        "merge":   SCRIPTS_DIR / "merge_lora.py",
        "convert": SCRIPTS_DIR / "convert_lora.py",
    }
    script = script_map.get(kind)
    if not script or not script.exists():
        raise HTTPException(404, f"{kind}_script not found: {script}")

    cmd = None
    if kind == "train":
        if args.get("train_all"):
            cmd = ["python3", str(script), "--train_all"]
        else:
            mk = args.get("model_key")
            if not mk:
                raise HTTPException(400, "model_key required for train (or set train_all)")
            cmd = ["python3", str(script), "--model_key", mk]
        if args.get("force"):
            cmd.append("--force")
        if args.get("data_path"):
            cmd += ["--data_path", args["data_path"]]

    elif kind == "merge":
        if args.get("merge_all"):
            cmd = ["python3", str(script), "--merge_all"]
        else:
            mk = args.get("model_key")
            if not mk:
                raise HTTPException(400, "model_key required for merge (or set merge_all)")
            cmd = ["python3", str(script), "--model_key", mk]
        if args.get("force"):
            cmd.append("--force")

    elif kind == "convert":
        if args.get("convert_all"):
            cmd = ["python3", str(script), "--convert_all"]
        else:
            mk = args.get("model_key")
            if not mk:
                raise HTTPException(400, "model_key required for convert (or set convert_all)")
            cmd = ["python3", str(script), "--model_key", mk]
        if args.get("force"):
            cmd.append("--force")
    else:
        raise HTTPException(400, "kind must be train|merge|convert")

    with open(log_path, "w", buffering=1) as lf:
        lf.write(f"### {kind} job {job_id} @ {datetime.now().isoformat()}\n")
        lf.write("$ " + " ".join(cmd) + "\n\n")

    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=open(log_path, "a"), stderr=subprocess.STDOUT, env=env)
    JOBS[job_id] = {
        "id": job_id, "kind": kind, "args": args, "cmd": cmd,
        "pid": proc.pid, "start_ts": time.time(), "end_ts": None,
        "status": "running", "log": str(log_path),
    }

    def _watch():
        rc = proc.wait()
        j = JOBS.get(job_id)
        if j:
            j["end_ts"] = time.time()
            j["returncode"] = rc
            j["status"] = "ok" if rc == 0 else "error"

    threading.Thread(target=_watch, daemon=True).start()
    return JOBS[job_id]

# -----------------------------------------------------------------------------
# API models
# -----------------------------------------------------------------------------
class SwitchReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    mode: str            # chat|intent|small|util
    model_dir: str
    bounce: bool = True
    backend: str | None = None  # tgw|vllm|tabbyapi

class Knobs(BaseModel):
    model_config = {"protected_namespaces": ()}
    LLM_CHAT_API_BASE: str | None = None
    LLM_INTENT_API_BASE: str | None = None
    LLM_SMALL_API_BASE: str | None = None
    SMART_ASSISTANT_URL: str | None = None
    CUDA_VISIBLE_DEVICES: str | None = None
    PM2_CHAT: str | None = None
    PM2_INTENT: str | None = None
    PM2_SMALL: str | None = None
    OPENROUTER_API_BASE: str | None = None
    OPENAI_API_BASE: str | None = None
    DEFAULT_ROUTING_STRATEGY: str | None = None
    DEFAULT_FREE_FALLBACK_ALLOWED: str | None = None
    DEFAULT_PAID_FALLBACK_ALLOWED: str | None = None
    TGW_CHAT_WEBUI_ENABLED: str | None = None
    TGW_CHAT_WEBUI_PORT: str | None = None
    TGW_CHAT_WEBUI_BIND_HOST: str | None = None
    TGW_CHAT_WEBUI_PUBLIC_URL: str | None = None


class ChatTgwWebUiReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    enabled: bool
    restart: bool = True
    port: int | None = Field(default=None, ge=1, le=65535)
    bind_host: str | None = None
    public_url: str | None = None

class JobStart(BaseModel):
    model_config = {"protected_namespaces": ()}
    kind: str
    model_key: str | None = None
    force: bool | None = None
    train_all: bool | None = None
    merge_all: bool | None = None
    convert_all: bool | None = None
    data_path: str | None = None


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


class OpenRouterFreeDiscoveryReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    refresh_catalog: bool = True
    include_rankings: bool = True
    include_curated: bool = False
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

# -----------------------------------------------------------------------------
# Routes: baseline (restore everything the old UI used)
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    env = read_env()
    provider_models = read_provider_models()
    provider_policies = read_provider_policies()
    info = {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "active": current_links(),
        "slot_backends": read_slot_backends(),
        "provider_config": {
            "models_loaded": bool(provider_models),
            "policies_loaded": bool(provider_policies),
        },
        "pm2": {"chat": env.get("PM2_CHAT"), "intent": env.get("PM2_INTENT"), "small": env.get("PM2_SMALL")},
        "api_bases": {"chat": env.get("LLM_CHAT_API_BASE"), "intent": env.get("LLM_INTENT_API_BASE"), "small": env.get("LLM_SMALL_API_BASE")},
    }
    # quick non-fatal pings (TGW exposes /v1/models, not /health)
    try:
        r = requests.get(env["LLM_CHAT_API_BASE"].rstrip("/") + "/v1/models", timeout=2)
        info["chat_up"] = (r.status_code == 200)
    except Exception:
        info["chat_up"] = False
    try:
        r = requests.get(env["LLM_SMALL_API_BASE"].rstrip("/") + "/v1/models", timeout=2)
        info["small_up"] = (r.status_code == 200)
    except Exception:
        info["small_up"] = False
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

    # Slot visibility (honour ENABLE_* from .env)
    slots_enabled = {
        "chat":   env.get("ENABLE_CHAT", "1") == "1",
        "intent": env.get("ENABLE_INTENT", "1") == "1",
        "small":  env.get("ENABLE_SMALL", "1") == "1",
    }

    # Per-model metadata (kind, loader, bpw)
    all_names = sorted(set(chat_list + intent_list + small_list))
    meta = inspect_batch(all_names)

    return {
        "chat": chat_list,
        "intent": intent_list,
        "small": small_list,
        "active": active,
        "slot_backends": read_slot_backends(),
        "slots_enabled": slots_enabled,
        "meta": meta,
    }

@app.post("/switch")
def switch(req: SwitchReq):
    return switch_model(req.mode, req.model_dir, req.bounce, req.backend)

@app.get("/knobs")
def get_knobs():
    return redacted_env(read_env())

@app.post("/knobs")
def set_knobs(k: Knobs):
    env = read_env()
    for k_, v in k.dict().items():
        if v is not None:
            env[k_] = v
    write_env(env)
    return redacted_env(env)


@app.get("/providers/models")
def providers_models():
    doc = read_provider_models()
    return {
        "models": doc,
        "version": _doc_version(doc),
        "source": str(PROVIDER_MODELS_PATH),
        "time": datetime.utcnow().isoformat() + "Z",
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


@app.get("/providers/state")
def providers_state():
    return {
        "state": read_provider_runtime_state(),
        "source": str(PROVIDER_STATE_PATH),
        "time": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/providers/state/provider-model-flags")
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
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "manual_only": True,
        "catalog_fetched_ts": cache.get("fetched_ts"),
        "rankings_fetched_ts": cache.get("rankings_fetched_ts"),
        "free_candidates": payload,
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


@app.post("/router/chat", response_model=RouterChatResponse)
def router_chat(req: RouterChatRequest):
    env = read_env()
    provider_models = read_provider_models()
    policies = read_provider_policies()
    _maybe_refresh_openrouter_catalog(env)
    strategy = _resolve_strategy(req, env, policies)
    chain = _candidate_chain_for_strategy(strategy, req, policies)
    allow_fallbacks = _resolve_bool_pref(
        req.provider_preferences.allow_fallbacks,
        bool(policies.get("defaults", {}).get("allow_fallbacks", True)),
    )

    routing_errors = []
    selected_provider = None
    selected_model = None
    selected_lane = None
    attempted_by_lane: dict[str, set[str]] = {}
    raw = None
    for idx, lane in enumerate(chain):
        excluded = attempted_by_lane.setdefault(lane, set())
        provider, model_id = _pick_catalog_model(lane, provider_models, req, excluded_models=excluded, policies=policies)
        if model_id in excluded:
            continue
        excluded.add(model_id)
        if _is_model_in_cooldown(provider, model_id):
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": {
                    "type": "cooldown",
                    "message": "model is currently in cooldown",
                    "retryable": True,
                },
            })
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
                routing_errors.append({
                    "lane": lane,
                    "provider": provider,
                    "model": model_id,
                    "error": overflow_error,
                })
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
            if idx == len(chain) - 1:
                break
            continue
        payload = _build_chat_payload(req, model_id)
        try:
            raw = _dispatch_provider_chat(provider, env, payload)
            selected_provider = provider
            selected_model = model_id
            selected_lane = lane
            _mark_provider_success(provider, model_id)
            break
        except Exception as e:
            normalized = _normalize_provider_error(provider, e)
            _mark_provider_failure(provider, model_id, normalized)
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": normalized,
            })
            if not allow_fallbacks:
                break
            if idx == len(chain) - 1:
                break

    if raw is None or selected_provider is None or selected_model is None:
        raise HTTPException(502, {
            "message": "No provider candidate succeeded",
            "strategy": strategy,
            "candidate_chain": chain,
            "errors": routing_errors,
        })

    usage_raw = raw.get("usage", {}) if isinstance(raw, dict) else {}
    usage = RouterUsage(
        prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
        total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
    )

    selected_backend = read_slot_backends().get("chat", "tgw") if selected_provider == "local" else "remote"
    active_chat = current_links().get("chat")
    selected_active = os.path.basename(active_chat.rstrip("/")) if active_chat else None

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
        "strategy": strategy,
        "candidate_chain": chain,
        "selected_lane": selected_lane,
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "selected_backend": selected_backend,
        "selected_active_local_model": selected_active,
        "outcome": "ok",
        "attempt_errors": routing_errors,
        "usage": usage.dict(),
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
    )
    _append_router_decision(decision)

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
    strategy = _resolve_strategy(req, env, policies)
    chain = _candidate_chain_for_strategy(strategy, req, policies)
    allow_fallbacks = _resolve_bool_pref(
        req.provider_preferences.allow_fallbacks,
        bool(policies.get("defaults", {}).get("allow_fallbacks", True)),
    )

    routing_errors = []
    selected_provider = None
    selected_model = None
    selected_lane = None
    attempted_by_lane: dict[str, set[str]] = {}
    raw = None
    for idx, lane in enumerate(chain):
        excluded = attempted_by_lane.setdefault(lane, set())
        provider, model_id = _pick_catalog_model(lane, provider_models, req, excluded_models=excluded, policies=policies)
        if model_id in excluded:
            continue
        excluded.add(model_id)
        if _is_model_in_cooldown(provider, model_id):
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": {
                    "type": "cooldown",
                    "message": "model is currently in cooldown",
                    "retryable": True,
                },
            })
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
                routing_errors.append({
                    "lane": lane,
                    "provider": provider,
                    "model": model_id,
                    "error": overflow_error,
                })
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
            if idx == len(chain) - 1:
                break
            continue
        payload = _build_completion_payload(req, model_id)
        try:
            raw = _dispatch_provider_completions(provider, env, payload)
            selected_provider = provider
            selected_model = model_id
            selected_lane = lane
            _mark_provider_success(provider, model_id)
            break
        except Exception as e:
            normalized = _normalize_provider_error(provider, e)
            _mark_provider_failure(provider, model_id, normalized)
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": normalized,
            })
            if not allow_fallbacks or idx == len(chain) - 1:
                break

    if raw is None or selected_provider is None or selected_model is None:
        raise HTTPException(502, {
            "message": "No provider candidate succeeded",
            "strategy": strategy,
            "candidate_chain": chain,
            "errors": routing_errors,
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

    now_ts = datetime.utcnow().isoformat() + "Z"
    decision = {
        "request_id": uuid.uuid4().hex[:12],
        "ts": now_ts,
        "strategy": strategy,
        "candidate_chain": chain,
        "selected_lane": selected_lane,
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "selected_backend": read_slot_backends().get("chat", "tgw") if selected_provider == "local" else "remote",
        "selected_active_local_model": None,
        "outcome": "ok",
        "attempt_errors": routing_errors,
        "usage": usage.dict(),
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
    )
    _append_router_decision(decision)

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
    strategy = _resolve_strategy(req, env, policies)
    chain = _candidate_chain_for_strategy(strategy, req, policies)
    allow_fallbacks = _resolve_bool_pref(
        req.provider_preferences.allow_fallbacks,
        bool(policies.get("defaults", {}).get("allow_fallbacks", True)),
    )

    routing_errors = []
    selected_provider = None
    selected_model = None
    selected_lane = None
    attempted_by_lane: dict[str, set[str]] = {}
    raw = None
    for idx, lane in enumerate(chain):
        excluded = attempted_by_lane.setdefault(lane, set())
        provider, model_id = _pick_catalog_model(lane, provider_models, req, excluded_models=excluded, policies=policies)
        if model_id in excluded:
            continue
        excluded.add(model_id)
        if _is_model_in_cooldown(provider, model_id):
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": {
                    "type": "cooldown",
                    "message": "model is currently in cooldown",
                    "retryable": True,
                },
            })
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
                routing_errors.append({
                    "lane": lane,
                    "provider": provider,
                    "model": model_id,
                    "error": overflow_error,
                })
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
            if idx == len(chain) - 1:
                break
            continue
        payload = _build_embed_payload(req, model_id)
        try:
            raw = _dispatch_provider_embeddings(provider, env, payload)
            selected_provider = provider
            selected_model = model_id
            selected_lane = lane
            _mark_provider_success(provider, model_id)
            break
        except Exception as e:
            normalized = _normalize_provider_error(provider, e)
            _mark_provider_failure(provider, model_id, normalized)
            routing_errors.append({
                "lane": lane,
                "provider": provider,
                "model": model_id,
                "error": normalized,
            })
            if not allow_fallbacks or idx == len(chain) - 1:
                break

    if raw is None or selected_provider is None or selected_model is None:
        raise HTTPException(502, {
            "message": "No provider candidate succeeded",
            "strategy": strategy,
            "candidate_chain": chain,
            "errors": routing_errors,
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

    now_ts = datetime.utcnow().isoformat() + "Z"
    decision = {
        "request_id": uuid.uuid4().hex[:12],
        "ts": now_ts,
        "strategy": strategy,
        "candidate_chain": chain,
        "selected_lane": selected_lane,
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "selected_backend": read_slot_backends().get("chat", "tgw") if selected_provider == "local" else "remote",
        "selected_active_local_model": None,
        "outcome": "ok",
        "attempt_errors": routing_errors,
        "usage": usage.dict(),
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
    )
    _append_router_decision(decision)

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
    by_error_type = {}
    by_provider = {}

    for d in recent:
        if not isinstance(d, dict):
            continue
        chain = d.get("candidate_chain", [])
        attempts = d.get("attempt_errors", [])
        provider = d.get("selected_provider", "unknown")
        by_provider[provider] = by_provider.get(provider, 0) + 1

        if isinstance(chain, list) and len(chain) > 1:
            with_fallback_attempts += 1
        if isinstance(attempts, list) and attempts:
            with_attempt_errors += 1
            for a in attempts:
                err = a.get("error", {}) if isinstance(a, dict) else {}
                et = err.get("type", "unknown") if isinstance(err, dict) else "unknown"
                by_error_type[et] = by_error_type.get(et, 0) + 1

    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "window_count": total,
        "with_fallback_chain": with_fallback_attempts,
        "with_attempt_errors": with_attempt_errors,
        "by_error_type": by_error_type,
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
    if mode not in ("chat", "intent", "small"):
        raise HTTPException(400, "mode must be chat|intent|small")
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
    try:
        return _test_openai(env["LLM_CHAT_API_BASE"], "chat_active_model", q, max_tokens=64, no_thinking=no_thinking)
    except Exception as e:
        raise HTTPException(502, f"chat api error: {e}")

@app.get("/test-intent")
def test_intent(q: str = "Return ONLY the word OK.", no_thinking: bool = False):
    env = read_env()
    try:
        return _test_openai(env["LLM_INTENT_API_BASE"], "intent_active_model", q, max_tokens=32, no_thinking=no_thinking)
    except Exception as e:
        raise HTTPException(502, f"intent api error: {e}")

@app.get("/test-util")
def test_util(q: str = "Say OK and nothing else.", no_thinking: bool = False):
    env = read_env()
    try:
        return _test_openai(env["LLM_SMALL_API_BASE"], "small_active_model", q, max_tokens=32, no_thinking=no_thinking)
    except Exception as e:
        raise HTTPException(502, f"small api error: {e}")

# -----------------------------------------------------------------------------
# Jobs endpoints (train/merge/convert)
# -----------------------------------------------------------------------------
@app.get("/jobs")
def jobs_list():
    out = []
    for j in JOBS.values():
        out.append({
            "id": j["id"], "kind": j["kind"], "status": j["status"],
            "start_ts": j["start_ts"], "end_ts": j.get("end_ts"),
            "pid": j["pid"], "returncode": j.get("returncode"),
            "log": j["log"], "args": j["args"]
        })
    out.sort(key=lambda x: x["start_ts"], reverse=True)
    return out

@app.get("/jobs/{job_id}")
def jobs_detail(job_id: str, tail: int = 120):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return {**j, "tail": _tail(Path(j["log"]), n=tail)}

@app.post("/jobs")
def jobs_start(req: JobStart):
    return _launch_job(req.kind, req.dict())

@app.post("/jobs/{job_id}/cancel")
def jobs_cancel(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    try:
        os.kill(j["pid"], signal.SIGTERM)
        j["status"] = "cancelling"
        return {"ok": True}
    except ProcessLookupError:
        j["status"] = "ended"
        return {"ok": False, "detail": "process already ended"}

# -----------------------------------------------------------------------------
# Newer: Engines endpoints for the new Engine Controls UI
# -----------------------------------------------------------------------------
def _engine_def(mode: str) -> dict:
    env = read_env()
    if mode == "chat":
        return {"mode": "chat", "unit": os.getenv("SYSTEMD_LLM_A") or "", "base": env["LLM_CHAT_API_BASE"], "port": 8500}
    if mode == "intent":
        return {"mode": "intent", "unit": os.getenv("SYSTEMD_LLM_B") or "", "base": env["LLM_INTENT_API_BASE"], "port": 8501}
    if mode == "small":
        return {"mode": "small", "unit": os.getenv("SYSTEMD_LLM_C") or "", "base": env["LLM_SMALL_API_BASE"], "port": 8502}
    raise HTTPException(400, "mode must be chat|intent|small")

@app.get("/engines/status")
def engines_status(request: Request):
    env = read_env()
    chat = _engine_def("chat")
    intent = _engine_def("intent")
    small = _engine_def("small")
    slot_backends = read_slot_backends()

    def pack(e: dict) -> dict:
        unit = e["unit"]
        backend = slot_backends.get(e["mode"], "tgw")
        payload = {
            "unit": unit or "(not set)",
            "backend": backend,
            "base": e["base"],
            "port": e["port"],
            "listening": _is_listening(e["port"]),
            "systemd": _systemctl_show(unit) if unit else {"error": "SYSTEMD unit not configured"},
            "active": current_links().get(e["mode"]),
        }
        if e["mode"] == "chat":
            payload["tgw_webui"] = _chat_tgw_webui_state(env=env, request=request, backend=backend)
        return payload

    return {"chat": pack(chat), "intent": pack(intent), "small": pack(small), "time": datetime.utcnow().isoformat() + "Z"}


@app.post("/engines/chat/tgw-webui")
def engines_chat_tgw_webui(req: ChatTgwWebUiReq, request: Request):
    backend = read_slot_backends().get("chat", "tgw")
    if backend != "tgw":
        raise HTTPException(400, f"chat slot backend is '{backend}'; TGW WebUI only works on the tgw lane")

    env = read_env()
    current = _chat_tgw_webui_config(env)
    env["TGW_CHAT_WEBUI_ENABLED"] = "1" if req.enabled else "0"
    env["TGW_CHAT_WEBUI_PORT"] = str(req.port if req.port is not None else current["port"])

    bind_host = (req.bind_host if req.bind_host is not None else current["bind_host"]).strip() or "127.0.0.1"
    env["TGW_CHAT_WEBUI_BIND_HOST"] = bind_host

    if req.public_url is not None:
        cleaned_public_url = req.public_url.strip()
        if cleaned_public_url:
            env["TGW_CHAT_WEBUI_PUBLIC_URL"] = cleaned_public_url
        else:
            env.pop("TGW_CHAT_WEBUI_PUBLIC_URL", None)

    write_env(env)

    restarted = False
    if req.restart:
        unit = _engine_def("chat").get("unit")
        if not unit:
            raise HTTPException(400, "SYSTEMD unit not configured for chat (SYSTEMD_LLM_A)")
        _systemctl_restart(unit)
        restarted = True

    current_env = read_env()
    return {
        "ok": True,
        "mode": "chat",
        "restarted": restarted,
        "tgw_webui": _chat_tgw_webui_state(env=current_env, request=request, backend=backend),
    }

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
    target = _engine_def(mode)
    units = {
        "chat": os.getenv("SYSTEMD_LLM_A") or "",
        "intent": os.getenv("SYSTEMD_LLM_B") or "",
        "small": os.getenv("SYSTEMD_LLM_C") or "",
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
