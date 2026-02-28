# convert_lora.py

import os
import shutil
from pathlib import Path
import argparse
import json
import hashlib

# --- Load config ---
with open("model_configs.json") as f:
    all_configs = json.load(f)

# --- Helper: SHA256 hash a file ---
def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

# --- Menu Helper ---
def choose_model():
    keys = list(all_configs.keys())
    print("\n📦 Available Merged Models to Convert:")
    for i, key in enumerate(keys):
        print(f"  {i+1}. {key}")
    print(f"  {len(keys)+1}. 🔁 Convert ALL")
    while True:
        try:
            choice = int(input("\nSelect a model to convert (number): "))
            if 1 <= choice <= len(keys):
                return keys[choice - 1], False
            elif choice == len(keys) + 1:
                return None, True
        except ValueError:
            pass
        print("❌ Invalid selection. Try again.")

# --- Convert Logic ---
def convert_to_exl2(model_key, force=False):
    config = all_configs[model_key]

    source = Path(f"output/merged_{model_key}")
    dest = Path(f"output/lora_{model_key}")
    script_path = Path(config.get("convert_script_path", str(EXLLAMA_ROOT) + "/convert.py"))
    bits = config.get("convert_bits", 6.5)
    groupsize = config.get("convert_groupsize", 2048)

    if not source.exists():
        print(f"❌ Skipping {model_key}: Merged folder does not exist: {source}")
        return

    # Find safetensors file in merged folder
    safetensors_files = list(source.glob("*.safetensors"))
    if not safetensors_files:
        print(f"❌ Skipping {model_key}: No .safetensors file found in {source}")
        return

    safetensors_file = max(safetensors_files, key=lambda f: f.stat().st_size)
    current_hash = hash_file(safetensors_file)
    hash_path = dest / "converted_from_merge_hash.txt"

    if hash_path.exists() and not force:
        with open(hash_path, "r") as f:
            existing_hash = f.read().strip()
        if existing_hash == current_hash:
            print(f"⏩ Skipping {model_key}: already converted from this merged model. Use --force to re-convert.")
            return

    print(f"\n🔁 Converting: {model_key}")
    dest.mkdir(parents=True, exist_ok=True)

    cmd = (
        f"CUDA_VISIBLE_DEVICES={CUDA_DEVICES} "
        f"python3 {script_path} "
        f"-i {source} "
        f"-o {dest} "
        f"-b {bits} "
        f"-ss {groupsize} "
    )


    print(f"[⚙️] Running conversion command:\n{cmd}\n")
    os.system(cmd)

    for fname in ["config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json", "tokenizer.model"]:
        src = source / fname
        if src.exists():
            shutil.copy(src, dest / fname)
            print(f"[✓] Copied {fname} to {dest}")

    with open(hash_path, "w") as f:
        f.write(current_hash)

    print(f"✅ Done: EXL2 model saved to {dest}")

if __name__ == "__main__":
    # --- CLI Args ---
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_key", help="Convert a specific model to EXL2")
    parser.add_argument("--convert_all", action="store_true", help="Convert all merged models")
    parser.add_argument("--force", action="store_true", help="Force reconversion even if hash matches")
    args = parser.parse_args()

    # --- Determine which to convert ---
    if args.convert_all:
        keys_to_convert = list(all_configs.keys())
    elif args.model_key:
        keys_to_convert = [args.model_key]
    else:
        chosen_key, do_all = choose_model()
        keys_to_convert = list(all_configs.keys()) if do_all else [chosen_key]

    # --- Run conversions ---
    for key in keys_to_convert:
        convert_to_exl2(key, force=args.force)
