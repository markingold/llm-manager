#!/usr/bin/env python3
"""
launch_tgw.py - Normalize and launch text-generation-webui for a slot.

Default behavior is slot-safe and API-only (no WebUI). Standalone WebUI mode
can be enabled explicitly with --webui.
"""

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "api"))
from model_inspector import detect_kind, detect_loader

MODELS_DIR = os.getenv(
    "SERVER_MODELS_DIR",
    os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"),
)
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / "secrets" / ".env"
GLOBAL_ENV_PATH = pathlib.Path(
    os.getenv("LLM_MANAGER_GLOBAL_ENV_PATH", "/srv/2bananas/secrets/global.env")
)


def read_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_runtime_env() -> dict:
    env = dict(os.environ)

    # Shared global env keys are preferred when not already set by process env.
    for key, value in read_env_file(GLOBAL_ENV_PATH).items():
        if value and key not in env:
            env[key] = value

    # Project-local secrets provide fallback values when global/env do not define one.
    for key, value in read_env_file(ENV_PATH).items():
        if key not in env:
            env[key] = value
    return env


def env_flag(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def env_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return default
    return parsed if parsed > 0 else default


def main():
    parser = argparse.ArgumentParser(description="Launch text-generation-webui with auto loader detection")
    parser.add_argument("--api-port", required=True, help="API listen port")
    parser.add_argument("--model", required=True, help="Model directory name or symlink in model dir")
    parser.add_argument("--max-seq-len", required=True, help="Context length")
    parser.add_argument("--listen-host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--model-dir", default=MODELS_DIR, help="Models directory")
    webui_mode = parser.add_mutually_exclusive_group()
    webui_mode.add_argument("--webui", action="store_true", help="Enable TGW WebUI for this process")
    webui_mode.add_argument("--no-webui", action="store_true", help="Force API-only mode for this process")
    args = parser.parse_args()

    runtime_env = read_runtime_env()
    if args.webui:
        tgw_webui_enabled = True
    elif args.no_webui:
        tgw_webui_enabled = False
    else:
        tgw_webui_enabled = env_flag(
            runtime_env.get("TGW_WEBUI_ENABLED", runtime_env.get("TGW_CHAT_WEBUI_ENABLED")),
            False,
        )

    tgw_webui_port = env_int(
        runtime_env.get("TGW_WEBUI_PORT", runtime_env.get("TGW_CHAT_WEBUI_PORT")),
        7860,
    )
    tgw_webui_bind_host = str(
        runtime_env.get("TGW_WEBUI_BIND_HOST", runtime_env.get("TGW_CHAT_WEBUI_BIND_HOST"))
        or args.listen_host
    ).strip() or args.listen_host

    model_path = pathlib.Path(args.model_dir) / args.model
    resolved = model_path.resolve()

    kind = detect_kind(resolved)
    loader = detect_loader(kind)

    print(
        f"[launch-tgw] model={args.model} resolved={resolved.name} kind={kind} loader={loader} webui={'on' if tgw_webui_enabled else 'off'}",
        flush=True,
    )

    cmd = [
        sys.executable,
        "server.py",
        "--api",
        "--api-port",
        str(args.api_port),
        "--listen",
        "--listen-host",
        tgw_webui_bind_host,
        "--extensions",
        "openai",
        "--old-colors",
        "--loader",
        loader,
        "--model-dir",
        str(args.model_dir),
        "--model",
        args.model,
    ]

    if tgw_webui_enabled:
        cmd += ["--listen-port", str(tgw_webui_port)]
    else:
        cmd += ["--nowebui"]

    if loader == "llama.cpp":
        cmd += ["--ctx-size", str(args.max_seq_len)]
    else:
        cmd += ["--max_seq_len", str(args.max_seq_len)]

    os.execv(sys.executable, cmd)


if __name__ == "__main__":
    main()
