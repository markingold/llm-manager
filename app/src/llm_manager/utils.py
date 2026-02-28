"""
utils.py — Shared helpers for all CLI tools.

Centralises: env loading, config, hash, model chooser, dataset builder.
"""

import os, json, glob, random, hashlib
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Locate project root (two levels up from this file → llm-manager/)
# ---------------------------------------------------------------------------
_THIS = Path(__file__).resolve()
PROJECT_ROOT = _THIS.parents[3]          # llm-manager/

# ---------------------------------------------------------------------------
# Load .env (secrets/.env is canonical; dotenv also picks up a bare .env)
# ---------------------------------------------------------------------------
_env_path = PROJECT_ROOT / "secrets" / ".env"
load_dotenv(_env_path)

# ---------------------------------------------------------------------------
# Path constants derived from environment
# ---------------------------------------------------------------------------
WEBUI_ROOT       = Path(os.getenv("WEBUI_ROOT",       "/srv/2bananas/engines/text-generation-webui"))
WEBUI_MODELS_DIR = Path(os.getenv("WEBUI_MODELS_DIR", "/srv/2bananas/engines/models"))
EXLLAMA_ROOT     = Path(os.getenv("EXLLAMA_ROOT",     "/srv/2bananas/engines/exllamav2"))
CUDA_DEVICES     = os.getenv("CUDA_VISIBLE_DEVICES", "0")
PM2_CHAT         = os.getenv("PM2_CHAT",   "llm_a_8500")
PM2_INTENT       = os.getenv("PM2_INTENT", "llm_b_8501")
PM2_SMALL        = os.getenv("PM2_SMALL",  "llm_c_8502")
HF_TOKEN         = os.getenv("HF_TOKEN")

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
CONFIG_PATH = PROJECT_ROOT / "model_configs.json"

def load_configs() -> dict:
    """Load model_configs.json relative to project root."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# SHA-256 hashing
# ---------------------------------------------------------------------------
def hash_file(filepath) -> str:
    """SHA-256 of a file (streaming, low memory)."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

# ---------------------------------------------------------------------------
# Interactive model chooser
# ---------------------------------------------------------------------------
def choose_model(configs: dict | None = None, label="Available Models") -> tuple:
    """
    Interactive prompt: returns (key, do_all).
    If user picks a single model: (key, False).
    If user picks 'All':          (None, True).
    """
    if configs is None:
        configs = load_configs()
    keys = list(configs.keys())
    print(f"\n📚 {label}:")
    for i, key in enumerate(keys):
        print(f"  {i+1}. {key}")
    print(f"  {len(keys)+1}. 🔁 All")
    while True:
        try:
            choice = int(input("\nSelect an option (number): "))
            if 1 <= choice <= len(keys):
                return keys[choice - 1], False
            if choice == len(keys) + 1:
                return None, True
        except ValueError:
            pass
        print("❌ Invalid selection. Try again.")

# ---------------------------------------------------------------------------
# Combined-dataset builder (single canonical copy)
# ---------------------------------------------------------------------------
def build_combined_dataset(
    pattern: str | None = None,
    output_path: str = "data/combined_intent_data.jsonl",
) -> str:
    """
    Glob all *_prompts.jsonl files, validate JSON, shuffle, and write to
    *output_path*.  Also saves a timestamped snapshot.
    Returns the output path string.
    """
    if pattern is None:
        pattern = str(PROJECT_ROOT / "data" / "*_prompts.jsonl")

    files = glob.glob(pattern)
    combined: list[str] = []

    for path in files:
        with open(path, encoding="utf-8") as f:
            for num, line in enumerate(f, 1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError as e:
                    # Fallback: try to rescue unescaped inner JSON
                    if (raw.startswith("{") and '"prompt":' in raw
                            and ',"response":' in raw and raw.endswith('"}')):
                        try:
                            prefix, resp_part = raw.split(',"response":"', 1)
                            prompt_prefix = '{"prompt":"'
                            if not prefix.startswith(prompt_prefix):
                                raise ValueError()
                            prompt_text = prefix[len(prompt_prefix):]
                            if not resp_part.endswith('"}'):
                                raise ValueError()
                            resp_json_str = resp_part[:-2]
                            json.loads(resp_json_str)   # validate
                            rec = {
                                "prompt": prompt_text,
                                "response": json.dumps(
                                    json.loads(resp_json_str), ensure_ascii=False
                                ),
                            }
                        except Exception:
                            raise ValueError(
                                f"Invalid JSON in {path}:{num}\n{e}\n{raw}"
                            ) from None
                    else:
                        raise ValueError(
                            f"Invalid JSON in {path}:{num}\n{e}\n{raw}"
                        ) from None

                # Ensure response is always a string
                if isinstance(rec.get("response"), dict):
                    rec["response"] = json.dumps(rec["response"], ensure_ascii=False)
                combined.append(json.dumps(rec, ensure_ascii=False))

    if not combined:
        raise ValueError("No data found in any *_prompts.jsonl files.")

    random.shuffle(combined)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(combined) + "\n", encoding="utf-8")

    snapshot = f"data/combined_intent_data_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    Path(snapshot).write_text("\n".join(combined) + "\n", encoding="utf-8")

    print(f"✅ Combined {len(files)} files into {output_path} ({len(combined)} items)")
    print(f"🕒 Snapshot saved to {snapshot}")
    return output_path
