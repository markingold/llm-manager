#!/usr/bin/env python3
"""
launch_tgw.py - Normalize and launch text-generation-webui for a slot.

This keeps TGW launch behavior in one wrapper while backend abstraction is added.
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


def read_runtime_env() -> dict:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            env[key.strip()] = value.strip()
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
    args = parser.parse_args()

    runtime_env = read_runtime_env()
    model_name = pathlib.Path(args.model).name
    chat_webui_enabled = model_name == "chat_active_model" and env_flag(
        runtime_env.get("TGW_CHAT_WEBUI_ENABLED"),
        False,
    )
    chat_webui_port = env_int(runtime_env.get("TGW_CHAT_WEBUI_PORT"), 7860)
    chat_webui_bind_host = str(runtime_env.get("TGW_CHAT_WEBUI_BIND_HOST") or args.listen_host).strip() or args.listen_host

    model_path = pathlib.Path(args.model_dir) / args.model
    resolved = model_path.resolve()

    kind = detect_kind(resolved)
    loader = detect_loader(kind)

    print(
        f"[launch-tgw] model={args.model} resolved={resolved.name} kind={kind} loader={loader} webui={'on' if chat_webui_enabled else 'off'}",
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
        chat_webui_bind_host,
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

    if chat_webui_enabled:
        cmd += ["--listen-port", str(chat_webui_port)]
    else:
        cmd += ["--nowebui"]

    if loader == "llama.cpp":
        cmd += ["--ctx-size", str(args.max_seq_len)]
    else:
        cmd += ["--max_seq_len", str(args.max_seq_len)]

    os.execv(sys.executable, cmd)


if __name__ == "__main__":
    main()
