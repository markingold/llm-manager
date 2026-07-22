#!/usr/bin/env python3
"""
launch_vllm.py - Normalize and launch vLLM OpenAI-compatible API server.
"""

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from llm_manager.model_inspector import detect_kind

MODELS_DIR = os.getenv(
    "SERVER_MODELS_DIR",
    os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"),
)
DEFAULT_VLLM_PYTHON_CANDIDATES = (
    "/srv/2bananas/engines/vllm-env/bin/python3",
    "/srv/2bananas/engines/vllm-env/bin/python",
    "/srv/2bananas/engines/llm-env/bin/python3",
    "/srv/2bananas/engines/llm-env/bin/python",
)


def _resolve_vllm_python_bin() -> str:
    configured = str(os.getenv("VLLM_PYTHON_BIN", "") or "").strip()
    candidates = []
    if configured:
        candidates.append(configured)
    candidates.extend(DEFAULT_VLLM_PYTHON_CANDIDATES)

    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = pathlib.Path(candidate)
        candidate_path = str(path)
        if candidate_path in seen:
            continue
        seen.add(candidate_path)
        if path.exists() and os.access(candidate_path, os.X_OK):
            return candidate_path

    return sys.executable


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
    parser.add_argument("--task", choices=("auto", "generate", "embed", "classify"), default="auto")
    args = parser.parse_args()

    models_root = pathlib.Path(args.model_dir).resolve()
    requested_model = pathlib.Path(args.model)
    model_path = requested_model if requested_model.is_absolute() else models_root / requested_model
    model_path = model_path.resolve()
    if model_path != models_root and not model_path.is_relative_to(models_root):
        raise SystemExit(f"[launch-vllm] model path escapes managed root: {model_path}")
    if not model_path.exists() or not model_path.is_dir():
        raise SystemExit(f"[launch-vllm] model path not found: {model_path}")
    model_kind = detect_kind(model_path)
    if model_kind not in {"transformers", "awq", "gptq", "lora"}:
        raise SystemExit(f"[launch-vllm] unsupported model kind: {model_kind}")
    model_arg = str(model_path)

    if args.cuda_visible_devices is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.cuda_visible_devices)

    vllm_python = _resolve_vllm_python_bin()

    cmd = [
        vllm_python,
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
        "--task",
        args.task,
    ]

    print(
        f"[launch-vllm] model={model_arg} port={args.api_port} tp={args.tensor_parallel_size} python={vllm_python}",
        flush=True,
    )

    os.execv(vllm_python, cmd)


if __name__ == "__main__":
    main()
