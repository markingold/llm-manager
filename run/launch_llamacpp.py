#!/usr/bin/env python3
"""Launch a managed llama.cpp OpenAI-compatible server for one GGUF model."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from llm_manager.backend_registry import resolve_llamacpp_server
from llm_manager.model_inspector import detect_kind

MODELS_DIR = os.getenv("SERVER_MODELS_DIR", os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"))


def _select_gguf(model_path: pathlib.Path, configured_name: str | None = None) -> pathlib.Path:
    if configured_name:
        candidate = (model_path / configured_name).resolve()
        if candidate.parent != model_path.resolve() or not candidate.is_file() or candidate.suffix.lower() != ".gguf":
            raise SystemExit(f"[launch-llamacpp] configured GGUF is invalid: {configured_name}")
        return candidate
    candidates = sorted(path for path in model_path.iterdir() if path.is_file() and path.suffix.lower() == ".gguf")
    if len(candidates) != 1:
        raise SystemExit(
            f"[launch-llamacpp] expected exactly one top-level GGUF in {model_path}, found {len(candidates)}; "
            "set LLAMA_CPP_MODEL_FILENAME to disambiguate"
        )
    return candidates[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch llama.cpp server")
    parser.add_argument("--api-port", required=True, type=int)
    parser.add_argument("--model", required=True, help="Managed model directory name or absolute path")
    parser.add_argument("--max-seq-len", default="8192")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--model-dir", default=MODELS_DIR)
    parser.add_argument("--cuda-visible-devices", default=None)
    parser.add_argument("--n-gpu-layers", default=os.getenv("LLAMA_CPP_N_GPU_LAYERS", "-1"))
    parser.add_argument("--parallel", default=os.getenv("LLAMA_CPP_PARALLEL", "1"))
    parser.add_argument("--tensor-split", default=os.getenv("LLAMA_CPP_TENSOR_SPLIT", ""))
    args = parser.parse_args()

    models_root = pathlib.Path(args.model_dir).resolve()
    requested = pathlib.Path(args.model)
    model_path = (requested if requested.is_absolute() else models_root / requested).resolve()
    if model_path != models_root and not model_path.is_relative_to(models_root):
        raise SystemExit(f"[launch-llamacpp] model path escapes managed root: {model_path}")
    if not model_path.is_dir():
        raise SystemExit(f"[launch-llamacpp] model directory not found: {model_path}")
    if detect_kind(model_path) != "gguf":
        raise SystemExit(f"[launch-llamacpp] unsupported model kind for {model_path.name}")

    gguf = _select_gguf(model_path, os.getenv("LLAMA_CPP_MODEL_FILENAME"))
    binary = resolve_llamacpp_server(dict(os.environ))
    if not binary:
        raise SystemExit("[launch-llamacpp] llama-server binary not found")
    if args.cuda_visible_devices is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.cuda_visible_devices)

    cmd = [
        binary,
        "--host", args.listen_host,
        "--port", str(args.api_port),
        "--model", str(gguf),
        "--alias", model_path.name,
        "--ctx-size", str(args.max_seq_len),
        "--n-gpu-layers", str(args.n_gpu_layers),
        "--parallel", str(args.parallel),
        "--jinja",
    ]
    if str(args.tensor_split).strip():
        cmd += ["--tensor-split", str(args.tensor_split).strip()]

    print(
        f"[launch-llamacpp] model={model_path.name} gguf={gguf.name} port={args.api_port} "
        f"gpu_layers={args.n_gpu_layers} parallel={args.parallel}",
        flush=True,
    )
    os.execv(binary, cmd)


if __name__ == "__main__":
    main()
