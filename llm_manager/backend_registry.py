"""Authoritative local inference-backend metadata and runtime probes."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENGINES_ROOT = Path("/srv/2bananas/engines")

BACKEND_SPECS: dict[str, dict[str, Any]] = {
    "tgw": {
        "label": "TextGen / text-generation-webui",
        "model_kinds": ("exl3", "gguf", "transformers"),
        "tasks": ("chat", "completions"),
        "readiness_path": "/v1/models",
        "readiness_shape": "openai_list",
        "served_model_id": "model_directory",
        "launcher": "launch_tgw.py",
    },
    "tabbyapi": {
        "label": "TabbyAPI",
        "model_kinds": ("exl2", "exl3"),
        "tasks": ("chat", "completions"),
        "readiness_path": "/v1/model",
        "readiness_shape": "current_model",
        "served_model_id": "model_directory",
        "launcher": "launch_tabbyapi.py",
    },
    "vllm": {
        "label": "vLLM",
        "model_kinds": ("transformers", "awq", "gptq", "lora"),
        "tasks": ("chat", "completions", "embeddings", "classification", "reranking"),
        "readiness_path": "/v1/models",
        "readiness_shape": "openai_list",
        "served_model_id": "model_directory_or_adapter",
        "launcher": "launch_vllm.py",
    },
    "llamacpp": {
        "label": "llama.cpp server",
        "model_kinds": ("gguf",),
        "tasks": ("chat", "completions"),
        "readiness_path": "/v1/models",
        "readiness_shape": "openai_list",
        "served_model_id": "model_directory",
        "launcher": "launch_llamacpp.py",
    },
}

SUPPORTED_BACKENDS = frozenset(BACKEND_SPECS)


def backend_spec(name: str) -> dict[str, Any] | None:
    key = str(name or "").strip().lower()
    spec = BACKEND_SPECS.get(key)
    return dict(spec) if isinstance(spec, dict) else None


def backend_supports_kind(name: str, model_kind: str) -> bool:
    spec = backend_spec(name)
    return bool(spec and str(model_kind or "unknown").strip().lower() in spec["model_kinds"])


def backend_supports_task(name: str, task: str) -> bool:
    spec = backend_spec(name)
    normalized = {
        "embed": "embeddings",
        "embedding": "embeddings",
        "completion": "completions",
        "classify": "classification",
        "rerank": "reranking",
    }.get(str(task or "").strip().lower(), str(task or "").strip().lower())
    return bool(spec and normalized in spec["tasks"])


def backend_readiness(name: str) -> tuple[str, str]:
    spec = backend_spec(name) or {}
    return str(spec.get("readiness_path") or "/v1/models"), str(spec.get("readiness_shape") or "openai_list")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_backend_pins(env: dict[str, str] | None = None) -> dict[str, Any]:
    source = env if isinstance(env, dict) else os.environ
    candidates = []
    configured = str(source.get("BACKEND_PINS_PATH") or "").strip()
    if configured:
        candidates.append(Path(configured))
    config_dir = str(source.get("LLM_MANAGER_CONFIG_DIR") or "").strip()
    if config_dir:
        candidates.append(Path(config_dir) / "backend-pins.json")
    candidates.append(SOURCE_ROOT / "config" / "backend-pins.json")
    for candidate in candidates:
        if candidate.is_file():
            return _read_json(candidate)
    return {}


def _run_version(command: list[str], timeout: int = 10) -> tuple[bool, str | None, str | None]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
    except Exception as exc:
        return False, None, str(exc)
    output = (getattr(result, "stdout", "") or getattr(result, "stderr", "") or "").strip()
    if result.returncode != 0:
        return False, None, output[-500:] or f"exit {result.returncode}"
    return True, output.splitlines()[-1].strip() if output else None, None


def vllm_python_candidates(env: dict[str, str] | None = None) -> list[str]:
    source = env if isinstance(env, dict) else os.environ
    configured = str(source.get("VLLM_PYTHON_BIN") or "").strip()
    rows = [configured] if configured else []
    rows.extend((
        "/srv/2bananas/engines/vllm-env/bin/python3",
        "/srv/2bananas/engines/vllm-env/bin/python",
        "/srv/2bananas/engines/llm-env/bin/python3",
        "/srv/2bananas/engines/llm-env/bin/python",
    ))
    return list(dict.fromkeys(row for row in rows if row))


def resolve_vllm_python(env: dict[str, str] | None = None, probe: bool = True) -> tuple[str | None, str | None]:
    for candidate in vllm_python_candidates(env):
        path = Path(candidate)
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        if not probe:
            return str(path), None
        ok, version, _ = _run_version([
            str(path),
            "-c",
            "import importlib.metadata as m; import vllm; print(m.version('vllm'))",
        ])
        if ok:
            return str(path), version
    return None, None


def resolve_llamacpp_server(env: dict[str, str] | None = None) -> str | None:
    source = env if isinstance(env, dict) else os.environ
    configured = str(source.get("LLAMA_CPP_SERVER_BIN") or "").strip()
    candidates = [configured] if configured else []
    candidates.extend((
        "/srv/2bananas/engines/text-generation-webui/venv/lib/python3.10/site-packages/llama_cpp_binaries/bin/llama-server",
        "/usr/local/bin/llama-server",
        "/usr/bin/llama-server",
        shutil.which("llama-server") or "",
    ))
    for candidate in dict.fromkeys(row for row in candidates if row):
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _git_revision(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    ok, revision, _ = _run_version(["git", "-C", str(path), "rev-parse", "HEAD"], timeout=5)
    return revision if ok else None


def _probe_tgw(env: dict[str, str]) -> dict[str, Any]:
    root = Path(str(env.get("WEBUI_ROOT") or DEFAULT_ENGINES_ROOT / "text-generation-webui")).expanduser().resolve()
    server = root / "server.py"
    python_candidates = [str(env.get("TGW_PYTHON_BIN") or "").strip(), str(root / "venv/bin/python"), sys.executable]
    python = next((row for row in python_candidates if row and Path(row).is_file() and os.access(row, os.X_OK)), None)
    available = server.is_file() and python is not None
    return {
        "available": available,
        "version": _git_revision(root),
        "command": [python, str(server)] if available else None,
        "root": str(root),
        "detail": None if available else "WEBUI_ROOT/server.py or TGW Python is unavailable",
    }


def _probe_tabbyapi(env: dict[str, str]) -> dict[str, Any]:
    pins = load_backend_pins(env)
    pin = pins.get("tabbyapi", {}) if isinstance(pins.get("tabbyapi"), dict) else {}
    expected = str(env.get("TABBYAPI_REVISION") or pin.get("revision") or "").strip() or None
    root = Path(str(env.get("TABBYAPI_ROOT") or DEFAULT_ENGINES_ROOT / "tabbyapi-src")).expanduser().resolve()
    actual = _git_revision(root)
    raw_cmd = str(env.get("TABBYAPI_CMD") or "").strip()
    command = shlex.split(raw_cmd) if raw_cmd else []
    executable = command[0] if command else ""
    command_available = bool(executable and (Path(executable).is_file() or shutil.which(executable)))
    revision_matches = expected is None or actual == expected
    available = command_available and revision_matches
    detail = None
    if not command_available:
        detail = "TABBYAPI_CMD is not configured or its executable is unavailable"
    elif not revision_matches:
        detail = f"TabbyAPI revision mismatch: expected {expected}, found {actual or 'unknown'}"
    return {
        "available": available,
        "version": actual,
        "command": command or None,
        "root": str(root),
        "revision_pin": expected,
        "revision_matches": revision_matches,
        "detail": detail,
    }


def _probe_vllm(env: dict[str, str]) -> dict[str, Any]:
    python, version = resolve_vllm_python(env, probe=True)
    return {
        "available": python is not None,
        "version": version,
        "command": [python, "-m", "vllm.entrypoints.openai.api_server"] if python else None,
        "root": str(Path(python).parent.parent) if python else None,
        "detail": None if python else "no configured Python can import vllm",
    }


def _probe_llamacpp(env: dict[str, str]) -> dict[str, Any]:
    binary = resolve_llamacpp_server(env)
    version = None
    if binary:
        _, version, _ = _run_version([binary, "--version"], timeout=5)
    return {
        "available": binary is not None,
        "version": version,
        "command": [binary] if binary else None,
        "root": str(Path(binary).parent) if binary else None,
        "detail": None if binary else "llama-server binary was not found",
    }


def probe_backend(name: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    key = str(name or "").strip().lower()
    spec = backend_spec(key)
    if spec is None:
        return {"backend": key, "available": False, "detail": "unknown backend"}
    source = dict(os.environ)
    if isinstance(env, dict):
        source.update({str(k): str(v) for k, v in env.items()})
    runtime = {
        "tgw": _probe_tgw,
        "tabbyapi": _probe_tabbyapi,
        "vllm": _probe_vllm,
        "llamacpp": _probe_llamacpp,
    }[key](source)
    return {
        "backend": key,
        "label": spec["label"],
        "model_kinds": list(spec["model_kinds"]),
        "tasks": list(spec["tasks"]),
        "readiness": {
            "path": spec["readiness_path"],
            "shape": spec["readiness_shape"],
        },
        "served_model_id": spec["served_model_id"],
        "launcher": spec["launcher"],
        **runtime,
    }


def probe_backends(env: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    return {name: probe_backend(name, env=env) for name in BACKEND_SPECS}


def public_backend_probe(row: dict[str, Any]) -> dict[str, Any]:
    """Remove executable arguments before returning a runtime probe over HTTP."""
    clean = dict(row)
    command = clean.pop("command", None)
    if isinstance(command, list) and command:
        clean["executable"] = str(command[0])
    return clean
