"""
model_inspector.py — Detect model format, loader, and recommended TGW flags.

Supports: EXL2, EXL3, GGUF, AWQ, GPTQ, FP16/BF16/FP8 safetensors, LoRA adapters.
"""

import json, os, re, pathlib, subprocess
from typing import Optional

MODELS_DIR = os.getenv(
    "SERVER_MODELS_DIR",
    os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"),
)

# ---------------------------------------------------------------------------
# Chat-template heuristics (from model name)
# ---------------------------------------------------------------------------
CHAT_TEMPLATE_GUESS = [
    (re.compile(r"(?i)qwen"),              "chatml"),
    (re.compile(r"(?i)llama[-_\s]?3"),     "llama-3"),
    (re.compile(r"(?i)llama"),             "llama"),
    (re.compile(r"(?i)mistral|mixtral"),   "mistral"),
    (re.compile(r"(?i)phi[-_\s]?[34]|phi"),"phi"),
    (re.compile(r"(?i)gemma"),             "gemma"),
    (re.compile(r"(?i)hermes"),            "chatml"),
]

# ---------------------------------------------------------------------------
# Format detection helpers
# ---------------------------------------------------------------------------

def _has_extension(model_path: pathlib.Path, ext: str) -> bool:
    """Check if any file with given extension exists (non-recursive for speed)."""
    return any(p.suffix.lower() == ext for p in model_path.iterdir() if p.is_file())


def _has_deep_extension(model_path: pathlib.Path, ext: str) -> bool:
    """Recursive check for extension."""
    return any(p.suffix.lower() == ext for p in model_path.rglob("*") if p.is_file())


def _read_json(path: pathlib.Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def detect_kind(model_path: pathlib.Path) -> str:
    """
    Detect the model format/kind.  Returns one of:
    exl2, exl3, gguf, awq, gptq, transformers, lora, unknown
    """
    name_lower = model_path.name.lower()

    # --- EXL2 / EXL3 (file extension is definitive) ---
    if _has_extension(model_path, ".exl2") or _has_deep_extension(model_path, ".exl2"):
        return "exl2"
    if _has_extension(model_path, ".exl3") or _has_deep_extension(model_path, ".exl3"):
        return "exl3"

    # --- GGUF ---
    if _has_extension(model_path, ".gguf"):
        return "gguf"

    # --- LoRA adapter ---
    if (model_path / "adapter_config.json").exists():
        return "lora"

    # --- Check config.json for quant method ---
    config = _read_json(model_path / "config.json")
    if config:
        qc = config.get("quantization_config", {})
        quant_method = qc.get("quant_method", "").lower()
        if quant_method == "awq":
            return "awq"
        if quant_method in ("gptq", "auto-gptq"):
            return "gptq"

    # --- Name-based hints ---
    if re.search(r"(?i)[-_]awq", name_lower):
        return "awq"
    if re.search(r"(?i)[-_]gptq", name_lower):
        return "gptq"
    if re.search(r"(?i)[-_]exl2", name_lower):
        return "exl2"
    if re.search(r"(?i)[-_]exl3", name_lower):
        return "exl3"
    if re.search(r"(?i)\.gguf", name_lower):
        return "gguf"

    # --- Standard transformers (safetensors or bin) ---
    if (model_path / "config.json").exists():
        return "transformers"

    return "unknown"


def detect_loader(kind: str) -> str:
    """Map model kind to the recommended TGW --loader flag."""
    return {
        "exl2":         "exllamav2",
        "exl3":         "exllamav2",    # exllamav2 >= 0.3 handles exl3
        "gguf":         "llama.cpp",
        "awq":          "exllamav2",    # exllamav2 supports AWQ natively
        "gptq":         "exllamav2",    # exllamav2 supports GPTQ natively
        "transformers": "transformers",
        "lora":         "transformers",
        "unknown":      "transformers",
    }.get(kind, "transformers")


def detect_dtype(model_path: pathlib.Path, kind: str) -> Optional[str]:
    """Try to determine dtype from config or filename."""
    config = _read_json(model_path / "config.json")
    if config:
        dtype = config.get("torch_dtype")
        if dtype:
            return dtype

    name = model_path.name.lower()
    if "fp16" in name:
        return "float16"
    if "bf16" in name:
        return "bfloat16"
    if "fp8" in name:
        return "float8_e4m3fn"
    if "fp32" in name:
        return "float32"
    return None


def detect_bpw(model_path: pathlib.Path) -> Optional[float]:
    """Try to extract bits-per-weight from name or measurement.json."""
    name = model_path.name
    # e.g. "4.0bpw", "6.5bpw", "6_5"
    m = re.search(r"(\d+)[._](\d+)\s*bpw", name, re.I)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    m = re.search(r"(\d+)\s*bpw", name, re.I)
    if m:
        return float(m.group(1))

    # Check measurement.json (exllamav2 output)
    meas = _read_json(model_path / "measurement.json")
    if meas and "bits" in meas:
        return float(meas["bits"])

    return None


def guess_chat_template(model_name: str, model_path: pathlib.Path) -> tuple[str, Optional[str]]:
    """
    Returns (mode, raw_template).
    mode is one of: "tokenizer", "chatml", "llama-3", "llama", "mistral", "phi", "gemma", "auto"
    raw_template is the Jinja2 template string if found in tokenizer_config.json.
    """
    tok_cfg = model_path / "tokenizer_config.json"
    if tok_cfg.exists():
        data = _read_json(tok_cfg)
        if data:
            tpl = data.get("chat_template")
            if isinstance(tpl, str) and tpl.strip():
                return "tokenizer", tpl

    for rx, tpl_name in CHAT_TEMPLATE_GUESS:
        if rx.search(model_name):
            return tpl_name, None

    return "auto", None


def estimate_vram_mb(model_path: pathlib.Path, kind: str, bpw: Optional[float]) -> Optional[int]:
    """Rough VRAM estimate for the model weights only (no KV cache)."""
    config = _read_json(model_path / "config.json")
    if not config:
        return None

    # Try to compute from parameter count
    num_params = config.get("num_parameters")
    if not num_params:
        # Estimate from architecture
        hidden = config.get("hidden_size", 0)
        layers = config.get("num_hidden_layers", 0)
        vocab = config.get("vocab_size", 0)
        if hidden and layers:
            # Rough: params ≈ 12 * layers * hidden^2 + vocab * hidden * 2
            num_params = 12 * layers * hidden * hidden + vocab * hidden * 2

    if not num_params:
        return None

    if bpw:
        bits = bpw
    elif kind in ("exl2", "exl3"):
        bits = 4.5  # reasonable default
    elif kind == "gguf":
        bits = 4.0
    elif kind in ("awq", "gptq"):
        bits = 4.0
    else:
        dtype = detect_dtype(model_path, kind)
        bits = {"float16": 16, "bfloat16": 16, "float32": 32, "float8_e4m3fn": 8}.get(dtype or "", 16)

    return int(num_params * bits / 8 / 1048576)


# ---------------------------------------------------------------------------
# Main inspect function
# ---------------------------------------------------------------------------

def inspect_one(model_name: str) -> dict:
    """Full inspection of a single model directory."""
    base = pathlib.Path(MODELS_DIR)
    model_path = base / model_name
    exists = model_path.exists() and model_path.is_dir()

    if not exists:
        return {
            "exists": False,
            "model_name": model_name,
            "path": str(model_path),
            "kind": "unknown",
            "loader": None,
            "bpw": None,
            "dtype": None,
            "chat_template_mode": "auto",
            "chat_template_raw": None,
            "vram_estimate_mb": None,
            "tgw_args": [],
        }

    kind = detect_kind(model_path)
    loader = detect_loader(kind)
    bpw = detect_bpw(model_path)
    dtype = detect_dtype(model_path, kind)
    tpl_mode, tpl_raw = guess_chat_template(model_name, model_path)
    vram = estimate_vram_mb(model_path, kind, bpw)

    # Build recommended TGW args
    tgw_args = [
        "--extensions", "openai",
        "--nowebui",
        "--listen", "--listen-host", "127.0.0.1",
        "--model-dir", str(base),
        "--model", model_name,
        "--loader", loader,
    ]

    if tpl_mode and tpl_mode != "auto":
        tgw_args += ["--chat-template", tpl_mode if tpl_mode != "tokenizer" else "auto"]

    return {
        "exists": True,
        "model_name": model_name,
        "path": str(model_path),
        "kind": kind,
        "loader": loader,
        "bpw": bpw,
        "dtype": dtype,
        "chat_template_mode": tpl_mode,
        "chat_template_raw": tpl_raw,
        "vram_estimate_mb": vram,
        "tgw_args": tgw_args,
    }


def inspect_batch(names: list[str]) -> dict:
    """Inspect multiple models at once."""
    return {name: inspect_one(name) for name in names}


def get_gpu_info() -> list[dict]:
    """Query nvidia-smi for GPU name, total/used/free VRAM."""
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return []
        gpus = []
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 6:
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "vram_total_mb": int(parts[2]),
                    "vram_used_mb": int(parts[3]),
                    "vram_free_mb": int(parts[4]),
                    "gpu_util_pct": int(parts[5]),
                })
        return gpus
    except Exception:
        return []
