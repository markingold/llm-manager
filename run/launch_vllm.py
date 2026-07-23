#!/usr/bin/env python3
"""
launch_vllm.py - Normalize and launch vLLM OpenAI-compatible API server.
"""

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from llm_manager.backend_registry import resolve_vllm_python
from llm_manager.model_inspector import detect_kind, resolve_lora_base_path

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
    resolved, _ = resolve_vllm_python(dict(os.environ), probe=True)
    if resolved:
        return resolved
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


def _effective_max_model_len(model_path: pathlib.Path, requested: str, task: str) -> str | None:
    """Clamp embedding context to checkpoint metadata or let vLLM infer it."""
    if task != "embed":
        return str(requested)
    try:
        config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
        declared = int(config.get("max_position_embeddings", 0) or 0)
        requested_int = int(str(requested))
    except Exception:
        return None
    if declared <= 0 or requested_int <= 0:
        return None
    return str(min(declared, requested_int))


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
    if model_kind not in {"transformers", "awq", "gptq"}:
        if model_kind != "lora":
            raise SystemExit(f"[launch-vllm] unsupported model kind: {model_kind}")

    adapter_path = model_path if model_kind == "lora" else None
    base_model_path = resolve_lora_base_path(model_path, models_root) if adapter_path else model_path
    if base_model_path is None:
        raise SystemExit("[launch-vllm] LoRA base_model_name_or_path is not available inside the managed model root")
    model_arg = str(base_model_path)
    served_model_name = base_model_path.name

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
        "--served-model-name",
        served_model_name,
        "--gpu-memory-utilization",
        str(args.gpu_memory_utilization),
        "--tensor-parallel-size",
        str(args.tensor_parallel_size),
    ]
    cmd += ["--runner", "pooling" if args.task in {"embed", "classify"} else "generate"]
    effective_max_model_len = _effective_max_model_len(base_model_path, str(args.max_seq_len), args.task)
    if effective_max_model_len is not None:
        cmd += ["--max-model-len", effective_max_model_len]
    if adapter_path is not None:
        cmd += ["--enable-lora", "--lora-modules", f"{adapter_path.name}={adapter_path}"]

    print(
        f"[launch-vllm] model={model_arg} served={served_model_name} adapter={adapter_path.name if adapter_path else '-'} "
        f"port={args.api_port} tp={args.tensor_parallel_size} python={vllm_python}",
        flush=True,
    )

    os.execv(vllm_python, cmd)


if __name__ == "__main__":
    main()
