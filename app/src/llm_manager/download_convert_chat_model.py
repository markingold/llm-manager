#!/usr/bin/env python3
"""
convert_chat_model.py
──────────────────────────────────────────────────────────────
• Pick a Hugging Face repo to download **or** select one you already
  downloaded into ./models/
• Convert its safetensors weights to ExLlama target format (EXL2 or EXL3)
• Output to:
    text-generation-webui/user_data/models/<repo>_<format>_b<bits>

> dependencies:
    pip install huggingface_hub python-dotenv
    (plus an ExLlama conversion script available on host)
"""

import os, sys, hashlib, argparse, shutil, subprocess, json
from pathlib import Path
from huggingface_hub import snapshot_download
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────
# Paths & defaults
# ─────────────────────────────────────────────────────────────
BASE_MODELS_DIR = Path(os.getenv('BASE_MODELS_DIR', 'models'))                               # raw HF downloads
WEBUI_MODELS_DIR = Path(os.getenv('WEBUI_MODELS_DIR', 'text-generation-webui/user_data/models'))
CONVERT_SCRIPT = Path(os.getenv('EXLLAMA_ROOT', 'exllamav2')) / 'convert.py'                 # adjust if needed
DEFAULT_GROUPSIZE = 2048

# ─────────────────────────────────────────────────────────────
# Hugging Face auth
# ─────────────────────────────────────────────────────────────
load_dotenv('secrets/.env')
HF_TOKEN = os.getenv("HF_TOKEN")  # leave unset for public repos

# ─────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────
def safe_folder_name(repo_id: str) -> str:
    """Replace '/' with '__' so each repo has a unique local dir."""
    return repo_id.replace("/", "__")

def hash_file(path: Path) -> str:
    """SHA-256 for large files."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()

def find_largest_safetensors(folder: Path) -> Path | None:
    tensors = list(folder.rglob("*.safetensors"))
    return max(tensors, key=lambda p: p.stat().st_size) if tensors else None

def write_meta(raw_dir: Path, repo_id: str):
    (raw_dir / "repo_id.txt").write_text(repo_id, encoding="utf-8")

def read_meta(raw_dir: Path) -> str | None:
    meta = raw_dir / "repo_id.txt"
    return meta.read_text().strip() if meta.exists() else None

# ─────────────────────────────────────────────────────────────
# Interactive menus
# ─────────────────────────────────────────────────────────────
def menu_choose_raw_model() -> tuple[str, Path]:
    BASE_MODELS_DIR.mkdir(exist_ok=True)
    existing_dirs = sorted([p for p in BASE_MODELS_DIR.iterdir() if p.is_dir()])

    print("\n📂 Existing raw models:")
    for idx, p in enumerate(existing_dirs, 1):
        label = read_meta(p) or p.name
        print(f"  {idx}. {label}")

    print(f"  {len(existing_dirs)+1}. 📥 Download new model")
    print(f"  {len(existing_dirs)+2}. ❌ Abort")

    while True:
        try:
            choice = int(input("\nSelect an option: "))
            if 1 <= choice <= len(existing_dirs):
                raw_dir = existing_dirs[choice-1]
                repo_id = read_meta(raw_dir) or raw_dir.name.replace("__", "/")
                return repo_id, raw_dir
            elif choice == len(existing_dirs)+1:
                repo_id = input("Paste full HF repo ID (e.g. Qwen/Qwen3-14B): ").strip()
                if repo_id:
                    return repo_id, None          # triggers download
            elif choice == len(existing_dirs)+2:
                sys.exit(0)
        except ValueError:
            pass
        print("❌ Invalid selection. Try again.")

def menu_choose_bits() -> float:
    print("\n🔢 Quantisation options:")
    print("  1. 6.5 bpw (smaller & faster, slight quality drop)")
    print("  2. 8   bpw (int8, near-FP16 quality)")
    while True:
        choice = input("Choose 1 or 2: ").strip()
        if choice == "1":
            return 6.5
        if choice == "2":
            return 8.0
        print("❌ Invalid choice.")

# ─────────────────────────────────────────────────────────────
# Core workflow
# ─────────────────────────────────────────────────────────────
def main():
    # ---- CLI (optional overrides) ----
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_id", help="HF repo (skips selection menu)")
    parser.add_argument("--bits",    type=float, help="6.5 or 8")
    parser.add_argument("--force",   action="store_true", help="Overwrite existing converted folder")
    parser.add_argument("--groupsize", type=int, default=DEFAULT_GROUPSIZE, help="Groupsize (default 2048)")
    parser.add_argument("--target_format", choices=["exl2", "exl3"], default="exl2", help="Target ExLlama format")
    parser.add_argument("--convert_script", help="Optional explicit path to convert.py")
    args = parser.parse_args()

    # ---- Gather inputs interactively if missing ----
    repo_id, raw_dir = (args.repo_id, None) if args.repo_id else (None, None)
    if not repo_id:
        repo_id, raw_dir = menu_choose_raw_model()
    if args.bits is not None and args.bits > 0:
        bits = args.bits
    else:
        bits = menu_choose_bits()

    target_format = (args.target_format or "exl2").strip().lower()
    convert_script = Path(args.convert_script) if args.convert_script else CONVERT_SCRIPT
    if not convert_script.exists():
        print(f"❌ Conversion script not found: {convert_script}")
        sys.exit(1)

    safe_name = safe_folder_name(repo_id)
    if raw_dir is None:
        raw_dir = BASE_MODELS_DIR / safe_name

    # ---- Download if needed ----
    if not raw_dir.exists():
        print(f"\n⬇️  Downloading {repo_id} → {raw_dir}")
        try:
            snapshot_download(
                repo_id=repo_id,
                local_dir=raw_dir,
                local_dir_use_symlinks=False,
                token=HF_TOKEN
            )
            write_meta(raw_dir, repo_id)
        except Exception as e:
            print(f"❌ Download failed: {e}")
            sys.exit(1)
    else:
        print(f"\n✅ Using existing download at {raw_dir}")

    # ---- Locate safetensors ----
    safetensors = find_largest_safetensors(raw_dir)
    if not safetensors:
        print("❌ No .safetensors files found – nothing to convert.")
        sys.exit(1)
    source_sha = hash_file(safetensors)

    # ---- Prepare destination ----
    bits_tag   = str(bits).replace(".", "p")      # 6.5 -> 6p5
    output_dir = WEBUI_MODELS_DIR / f"{safe_name}_{target_format}_b{bits_tag}"
    hash_path  = output_dir / "source_model_sha256.txt"

    if output_dir.exists() and not args.force:
        print(f"⏩ {output_dir} already exists. Use --force to overwrite.")
        sys.exit(0)

    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- Run conversion ----
    cmd = [
        sys.executable, str(convert_script),
        "-i", str(raw_dir),
        "-o", str(output_dir),
        "-b", str(bits),
    ]
    if target_format == "exl2":
        cmd += ["-ss", str(args.groupsize), "--res"]
    elif target_format == "exl3":
        work_dir = output_dir.parent / f".{output_dir.name}_work"
        # ExLlamaV3 checkpoint rotation can crash on some hosts; keep interval
        # effectively disabled so long conversions complete in one pass.
        cmd += ["-w", str(work_dir), "-cpi", "999999"]
    print(f"\n🔁 Converting to {target_format.upper()} ({bits} bpw)…\n{' '.join(cmd)}\n")
    ret = subprocess.call(cmd)
    if ret != 0:
        print("❌ Conversion script failed.")
        sys.exit(1)

    quant_outputs = list(output_dir.glob("*.safetensors")) + list(output_dir.glob("*.exl3"))
    if not quant_outputs:
        print(f"❌ Conversion did not produce quantized artifacts in {output_dir}")
        sys.exit(1)

    # ---- Copy tokenizer & config artefacts ----
    for fname in ["config.json", "generation_config.json",
                  "tokenizer.json", "tokenizer_config.json",
                  "tokenizer.model", "vocab.json", "merges.txt"]:
        src = raw_dir / fname
        if src.exists():
            shutil.copy(src, output_dir / fname)

    hash_path.write_text(source_sha)
    print(f"✅ Done!  Converted model saved to {output_dir}")
    print("   (Original model remains in", raw_dir, ")")

if __name__ == "__main__":
    main()
