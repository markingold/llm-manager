#!/usr/bin/env python3
"""
launch_vllm.py - Normalize and launch vLLM OpenAI-compatible API server.
"""

import argparse
import os
import pathlib
import sys

MODELS_DIR = os.getenv(
    "SERVER_MODELS_DIR",
    os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"),
)


def main():
    parser = argparse.ArgumentParser(description="Launch vLLM API server")
    parser.add_argument("--api-port", required=True, help="OpenAI API port")
    parser.add_argument("--model", required=True, help="Model directory name or absolute path")
    parser.add_argument("--max-seq-len", default="8192", help="Max model length")
    parser.add_argument("--listen-host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--model-dir", default=MODELS_DIR, help="Models directory for relative model names")
    parser.add_argument("--gpu-memory-utilization", default="0.9", help="vLLM GPU memory utilization")
    parser.add_argument("--tensor-parallel-size", default="1", help="vLLM tensor parallel size")
    parser.add_argument("--cuda-visible-devices", default=None, help="Optional CUDA_VISIBLE_DEVICES override")
    args = parser.parse_args()

    model_arg = args.model
    if not os.path.isabs(model_arg):
        model_arg = str(pathlib.Path(args.model_dir) / model_arg)

    if args.cuda_visible_devices is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.cuda_visible_devices)

    cmd = [
        sys.executable,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--host",
        args.listen_host,
        "--port",
        str(args.api_port),
        "--model",
        model_arg,
        "--max-model-len",
        str(args.max_seq_len),
        "--gpu-memory-utilization",
        str(args.gpu_memory_utilization),
        "--tensor-parallel-size",
        str(args.tensor_parallel_size),
    ]

    print(
        f"[launch-vllm] model={model_arg} port={args.api_port} tp={args.tensor_parallel_size}",
        flush=True,
    )

    os.execv(sys.executable, cmd)


if __name__ == "__main__":
    main()
