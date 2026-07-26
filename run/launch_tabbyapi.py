#!/usr/bin/env python3
"""
launch_tabbyapi.py - Normalize and launch a TabbyAPI-compatible ExLlama lane.

TabbyAPI command can vary by install; override with TABBYAPI_CMD when needed.
"""

import argparse
import os
import pathlib
import shlex
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from llm_manager.model_inspector import detect_kind
from llm_manager.runtime_env import read_runtime_env as _read_runtime_env

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNTIME_HOME = pathlib.Path(os.getenv("LLM_MANAGER_HOME", str(ROOT)))
SECRETS_DIR = pathlib.Path(os.getenv("LLM_MANAGER_SECRETS_DIR", str(RUNTIME_HOME / "secrets")))
ENV_PATH = SECRETS_DIR / ".env"
GLOBAL_ENV_PATH = pathlib.Path(
    os.getenv("LLM_MANAGER_GLOBAL_ENV_PATH", "/srv/2bananas/secrets/global.env")
)

MODELS_DIR = os.getenv(
    "SERVER_MODELS_DIR",
    os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"),
)


def read_runtime_env() -> dict[str, str]:
    return _read_runtime_env(
        project_env_path=ENV_PATH,
        global_env_path=GLOBAL_ENV_PATH,
    )


def main():
    parser = argparse.ArgumentParser(description="Launch TabbyAPI for EXL2/EXL3 serving")
    parser.add_argument("--api-port", required=True, help="OpenAI API port")
    parser.add_argument("--model", required=True, help="Model directory name or absolute path")
    parser.add_argument("--max-seq-len", default="8192", help="Context length")
    parser.add_argument("--listen-host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--model-dir", default=MODELS_DIR, help="Models directory for relative model names")
    parser.add_argument("--cuda-visible-devices", default=None, help="Optional CUDA_VISIBLE_DEVICES override")
    args = parser.parse_args()

    models_root = pathlib.Path(args.model_dir).resolve()
    requested_model = pathlib.Path(args.model)
    model_path = requested_model if requested_model.is_absolute() else models_root / requested_model
    model_path = model_path.resolve()
    if model_path != models_root and not model_path.is_relative_to(models_root):
        raise SystemExit(f"[launch-tabbyapi] model path escapes managed root: {model_path}")
    if not model_path.exists() or not model_path.is_dir():
        raise SystemExit(f"[launch-tabbyapi] model path not found: {model_path}")
    model_dir = str(model_path.parent)
    model_name = model_path.name

    if args.cuda_visible_devices is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.cuda_visible_devices)

    runtime_env = read_runtime_env()

    detected_kind = detect_kind(model_path)
    backend = ""
    if detected_kind == "exl3":
        backend = "exllamav3"
    elif detected_kind in {"exl2", "awq", "gptq"}:
        backend = "exllamav2"
    else:
        raise SystemExit(f"[launch-tabbyapi] unsupported model kind: {detected_kind}")

    base_cmd = str(runtime_env.get("TABBYAPI_CMD") or os.getenv("TABBYAPI_CMD") or "python -m tabbyapi").strip()
    cmd = shlex.split(base_cmd)
    cmd += [
        "--host",
        args.listen_host,
        "--port",
        str(args.api_port),
        "--model-name",
        model_name,
        "--model-dir",
        model_dir,
        "--max-seq-len",
        str(args.max_seq_len),
    ]
    if backend:
        cmd += ["--backend", backend]

    print(
        f"[launch-tabbyapi] model={model_name} model_dir={model_dir} kind={detected_kind} backend={backend or 'auto'} port={args.api_port}",
        flush=True,
    )
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
