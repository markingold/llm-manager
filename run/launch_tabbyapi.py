#!/usr/bin/env python3
"""
launch_tabbyapi.py - Normalize and launch a TabbyAPI-compatible ExLlama lane.

TabbyAPI command can vary by install; override with TABBYAPI_CMD when needed.
"""

import argparse
import os
import pathlib
import shlex

MODELS_DIR = os.getenv(
    "SERVER_MODELS_DIR",
    os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"),
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

    model_arg = args.model
    if not os.path.isabs(model_arg):
        model_arg = str(pathlib.Path(args.model_dir) / model_arg)

    if args.cuda_visible_devices is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.cuda_visible_devices)

    base_cmd = os.getenv("TABBYAPI_CMD", "python -m tabbyapi")
    cmd = shlex.split(base_cmd)
    cmd += [
        "--host",
        args.listen_host,
        "--port",
        str(args.api_port),
        "--model",
        model_arg,
        "--max-seq-len",
        str(args.max_seq_len),
    ]

    print(f"[launch-tabbyapi] model={model_arg} port={args.api_port}", flush=True)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
