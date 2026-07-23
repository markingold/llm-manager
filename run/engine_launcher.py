#!/usr/bin/env python3
"""
engine_launcher.py - Compatibility wrapper for existing systemd units.

Backend-specific launchers live under run/launch_*.py. This file remains the
stable systemd entrypoint and dispatches through the authoritative runtime
registry and persisted per-slot backend selection.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNTIME_HOME = pathlib.Path(os.getenv("LLM_MANAGER_HOME", str(ROOT)))
STATE_DIR = pathlib.Path(os.getenv("LLM_MANAGER_STATE_DIR", str(RUNTIME_HOME / "run" / "state")))
SLOT_BACKENDS_PATH = STATE_DIR / "slot_backends.json"
MODELS_DIR = os.getenv(
    "SERVER_MODELS_DIR",
    os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"),
)
SLOT_MODES = ("chat", "intent", "small", "embed")
DEFAULT_SLOT_BACKENDS = {"chat": "tgw", "intent": "tgw", "small": "tgw", "embed": "vllm"}
PORT_TO_MODE = {"8500": "chat", "8501": "intent", "8502": "small", "8503": "embed"}
DEFAULT_VLLM_PYTHON_CANDIDATES = (
    "/srv/2bananas/engines/vllm-env/bin/python3",
    "/srv/2bananas/engines/vllm-env/bin/python",
    "/srv/2bananas/engines/llm-env/bin/python3",
    "/srv/2bananas/engines/llm-env/bin/python",
)
MODEL_TO_MODE = {
    "chat_active_model": "chat",
    "intent_active_model": "intent",
    "small_active_model": "small",
    "embed_active_model": "embed",
}

sys.path.insert(0, str(ROOT))
try:
    from llm_manager.backend_registry import (
        SUPPORTED_BACKENDS,
        backend_supports_kind,
        probe_backend,
        resolve_vllm_python,
    )
    from llm_manager.model_inspector import detect_kind as _detect_kind
except Exception:
    SUPPORTED_BACKENDS = frozenset({"tgw", "vllm", "tabbyapi", "llamacpp"})
    backend_supports_kind = None
    probe_backend = None
    resolve_vllm_python = None
    _detect_kind = None


def _read_slot_backends() -> dict[str, str]:
    data = dict(DEFAULT_SLOT_BACKENDS)
    if not SLOT_BACKENDS_PATH.exists():
        return data
    try:
        raw = json.loads(SLOT_BACKENDS_PATH.read_text())
    except Exception:
        return data
    if not isinstance(raw, dict):
        return data
    for mode in SLOT_MODES:
        backend = str(raw.get(mode, "") or "").strip().lower()
        if backend in SUPPORTED_BACKENDS:
            data[mode] = backend
    return data


def _normalize_mode(mode: str | None) -> str:
    text = str(mode or "").strip().lower()
    if text == "util":
        return "small"
    return text if text in SLOT_MODES else "chat"


def _mode_for_launch(api_port: str, model: str) -> str:
    from_port = PORT_TO_MODE.get(str(api_port).strip())
    if from_port:
        return from_port

    model_text = str(model or "").strip()
    from_model = MODEL_TO_MODE.get(model_text)
    if from_model:
        return from_model
    if ":" in model_text:
        left = model_text.split(":", 1)[0].strip().lower()
        if left in {*SLOT_MODES, "util"}:
            return _normalize_mode(left)
    return "chat"


def _backend_for_mode(mode: str) -> str:
    mode_key = _normalize_mode(mode)
    env_key = f"LLM_{mode_key.upper()}_BACKEND"
    env_backend = str(os.getenv(env_key, "") or "").strip().lower()
    if env_backend in SUPPORTED_BACKENDS:
        return env_backend
    backends = _read_slot_backends()
    backend = str(backends.get(mode_key, DEFAULT_SLOT_BACKENDS[mode_key]) or "tgw").strip().lower()
    return backend if backend in SUPPORTED_BACKENDS else "tgw"


def _launcher_path_for_backend(backend: str) -> pathlib.Path:
    run_dir = pathlib.Path(__file__).resolve().parent
    name = {
        "tgw": "launch_tgw.py",
        "vllm": "launch_vllm.py",
        "tabbyapi": "launch_tabbyapi.py",
        "llamacpp": "launch_llamacpp.py",
    }.get(backend, "launch_tgw.py")
    path = run_dir / name
    if not path.exists() and backend != "tgw":
        return run_dir / "launch_tgw.py"
    return path


def _resolve_model_path(model: str) -> pathlib.Path:
    model_text = str(model or "").strip()
    model_path = pathlib.Path(model_text)
    if not model_path.is_absolute():
        model_path = pathlib.Path(MODELS_DIR) / model_text
    models_root = pathlib.Path(MODELS_DIR).resolve()
    resolved = model_path.resolve()
    if resolved != models_root and not resolved.is_relative_to(models_root):
        raise SystemExit(f"[engine-launcher] model path escapes managed root: {model_path}")
    if not resolved.exists() or not resolved.is_dir():
        raise SystemExit(f"[engine-launcher] model path not found: {model_path}")
    return resolved


def _model_kind_for_launch(model: str) -> str:
    model_path = _resolve_model_path(model)
    if _detect_kind is None:
        return "unknown"
    try:
        return str(_detect_kind(model_path) or "unknown").strip().lower() or "unknown"
    except Exception:
        return "unknown"


def _backend_supports_kind(backend: str, kind: str) -> bool:
    if backend_supports_kind is not None:
        return bool(backend_supports_kind(backend, kind))
    backend_key = str(backend or "").strip().lower()
    model_kind = str(kind or "unknown").strip().lower()
    if backend_key == "tgw":
        return model_kind in {"exl3", "gguf", "transformers"}
    if backend_key == "tabbyapi":
        return model_kind in {"exl2", "exl3"}
    if backend_key == "vllm":
        return model_kind in {"transformers", "awq", "gptq", "lora"}
    if backend_key == "llamacpp":
        return model_kind == "gguf"
    return False


def _vllm_available() -> bool:
    if resolve_vllm_python is not None:
        candidate, _ = resolve_vllm_python(dict(os.environ), probe=True)
        if candidate:
            os.environ["VLLM_PYTHON_BIN"] = candidate
            return True
        return False
    configured = str(os.getenv("VLLM_PYTHON_BIN", "") or "").strip()
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(DEFAULT_VLLM_PYTHON_CANDIDATES)

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = pathlib.Path(candidate)
        candidate_path = str(path)
        if candidate_path in seen:
            continue
        seen.add(candidate_path)
        if not path.exists() or not os.access(candidate_path, os.X_OK):
            continue
        try:
            probe = subprocess.run(
                [candidate_path, "-c", "import vllm"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except Exception:
            continue
        if probe.returncode == 0:
            os.environ["VLLM_PYTHON_BIN"] = candidate_path
            return True

    return False


def main():
    parser = argparse.ArgumentParser(description="Dispatch a managed model slot to its selected inference backend")
    parser.add_argument("--api-port", required=True, help="API listen port")
    parser.add_argument("--model", required=True, help="Model directory name (or symlink)")
    parser.add_argument("--max-seq-len", required=True, help="Max sequence length")
    parser.add_argument("--listen-host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--webui", action="store_true", help="Enable TGW WebUI for this launch")
    args = parser.parse_args()

    mode = _mode_for_launch(args.api_port, args.model)
    backend = _backend_for_mode(mode)
    model_kind = _model_kind_for_launch(args.model)
    if mode == "embed" and backend != "vllm":
        raise SystemExit(
            f"[engine-launcher] embedding slot requires vllm; configured_backend={backend}; "
            "persist the correct backend before starting the slot"
        )

    if not _backend_supports_kind(backend, model_kind):
        raise SystemExit(
            f"[engine-launcher] backend={backend} incompatible with model_kind={model_kind}; "
            "select and persist a compatible backend before starting the slot"
        )

    if backend == "vllm":
        if not _vllm_available():
            raise SystemExit("[engine-launcher] configured vllm backend is unavailable (module import failed)")
        else:
            vllm_python = str(os.getenv("VLLM_PYTHON_BIN", "") or "").strip()
            if vllm_python:
                print(f"[engine-launcher] backend=vllm using python={vllm_python}", flush=True)

    if backend in {"tabbyapi", "llamacpp"} and probe_backend is not None:
        runtime = probe_backend(backend, env=dict(os.environ))
        if not bool(runtime.get("available", False)):
            raise SystemExit(f"[engine-launcher] configured {backend} backend is unavailable: {runtime.get('detail')}")

    launcher = _launcher_path_for_backend(backend)

    cmd = [
        sys.executable,
        str(launcher),
        "--api-port",
        args.api_port,
        "--model", args.model,
        "--max-seq-len",
        args.max_seq_len,
        "--listen-host",
        args.listen_host,
    ]

    if backend == "tgw":
        if args.webui:
            cmd.append("--webui")
        else:
            cmd.append("--no-webui")
    else:
        slot_prefix = f"LLM_{mode.upper()}_"
        cuda_visible_devices = str(
            os.getenv(f"{slot_prefix}CUDA_VISIBLE_DEVICES", os.getenv("CUDA_VISIBLE_DEVICES", "")) or ""
        ).strip()
        if cuda_visible_devices:
            cmd += ["--cuda-visible-devices", cuda_visible_devices]
        if backend == "vllm" and mode == "embed":
            cmd += ["--task", "embed"]
        if backend == "vllm":
            gpu_memory = str(
                os.getenv(f"{slot_prefix}VLLM_GPU_MEMORY_UTILIZATION", os.getenv("VLLM_GPU_MEMORY_UTILIZATION", "0.9"))
            ).strip()
            tensor_parallel = str(
                os.getenv(f"{slot_prefix}VLLM_TENSOR_PARALLEL_SIZE", os.getenv("VLLM_TENSOR_PARALLEL_SIZE", "1"))
            ).strip()
            cmd += ["--gpu-memory-utilization", gpu_memory, "--tensor-parallel-size", tensor_parallel]
        if backend == "llamacpp":
            cmd += [
                "--n-gpu-layers",
                str(os.getenv(f"{slot_prefix}LLAMA_CPP_N_GPU_LAYERS", os.getenv("LLAMA_CPP_N_GPU_LAYERS", "-1"))),
                "--parallel",
                str(os.getenv(f"{slot_prefix}LLAMA_CPP_PARALLEL", os.getenv("LLAMA_CPP_PARALLEL", "1"))),
            ]
            tensor_split = str(
                os.getenv(f"{slot_prefix}LLAMA_CPP_TENSOR_SPLIT", os.getenv("LLAMA_CPP_TENSOR_SPLIT", ""))
            ).strip()
            if tensor_split:
                cmd += ["--tensor-split", tensor_split]

    print(
        f"[engine-launcher] mode={mode} backend={backend} kind={model_kind} launcher={launcher.name} model={args.model} port={args.api_port}",
        flush=True,
    )
    os.execv(sys.executable, cmd)


if __name__ == "__main__":
    main()
