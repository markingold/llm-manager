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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "api"))
from model_inspector import detect_kind

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / "secrets" / ".env"
GLOBAL_ENV_PATH = pathlib.Path(
    os.getenv("LLM_MANAGER_GLOBAL_ENV_PATH", "/srv/2bananas/secrets/global.env")
)

MODELS_DIR = os.getenv(
    "SERVER_MODELS_DIR",
    os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"),
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


def read_runtime_env() -> dict[str, str]:
    env = dict(os.environ)

    for key, value in read_env_file(GLOBAL_ENV_PATH).items():
        if value and key not in env:
            env[key] = value

    for key, value in read_env_file(ENV_PATH).items():
        if key not in env:
            env[key] = value

    return env


def main():
    parser = argparse.ArgumentParser(description="Launch TabbyAPI for EXL2/EXL3 serving")
    parser.add_argument("--api-port", required=True, help="OpenAI API port")
    parser.add_argument("--model", required=True, help="Model directory name or absolute path")
    parser.add_argument("--max-seq-len", default="8192", help="Context length")
    parser.add_argument("--listen-host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--model-dir", default=MODELS_DIR, help="Models directory for relative model names")
    parser.add_argument("--cuda-visible-devices", default=None, help="Optional CUDA_VISIBLE_DEVICES override")
    args = parser.parse_args()

    model_name = str(args.model)
    model_dir = str(args.model_dir)
    if os.path.isabs(model_name):
        model_path = pathlib.Path(model_name)
        model_name = model_path.name
        model_dir = str(model_path.parent)

    raw_model_path = pathlib.Path(model_dir) / model_name
    resolved_model_path = raw_model_path
    try:
        if raw_model_path.exists() or raw_model_path.is_symlink():
            resolved_model_path = raw_model_path.resolve()
    except Exception:
        resolved_model_path = raw_model_path

    if resolved_model_path.exists():
        model_dir = str(resolved_model_path.parent)
        model_name = resolved_model_path.name

    if args.cuda_visible_devices is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.cuda_visible_devices)

    runtime_env = read_runtime_env()

    model_path = pathlib.Path(model_dir) / model_name
    detected_kind = detect_kind(model_path)
    backend = ""
    if detected_kind == "exl3":
        backend = "exllamav3"
    elif detected_kind in {"exl2", "awq", "gptq"}:
        backend = "exllamav2"

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
