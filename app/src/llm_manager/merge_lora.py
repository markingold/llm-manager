# merge_lora.py

import os
import json
import argparse
import torch
import hashlib
from pathlib import Path
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
from safetensors.torch import save_file

# --- Load model config ---
with open("model_configs.json") as f:
    all_configs = json.load(f)

# --- Helper: Hash a file ---
def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

# --- Menu Helper ---
def choose_model():
    keys = list(all_configs.keys())
    print("\n📚 Available LoRA Models to Merge:")
    for i, key in enumerate(keys):
        print(f"  {i+1}. {key}")
    print(f"  {len(keys)+1}. 🔁 Merge ALL")
    while True:
        try:
            choice = int(input("\nSelect a model to merge (number): "))
            if 1 <= choice <= len(keys):
                return keys[choice - 1], False
            elif choice == len(keys) + 1:
                return None, True
        except ValueError:
            pass
        print("❌ Invalid selection. Try again.")

# --- Merge Function ---
def merge_lora_model(model_key, force=False):
    config = all_configs[model_key]
    base_model = config["model_name"]
    trust_remote_code = config.get("trust_remote_code", False)

    lora_path = f"./output/intent_{model_key}"
    merged_path = Path(f"./output/merged_{model_key}")
    lora_weights = os.path.join(lora_path, "adapter_model.safetensors")
    hash_path = merged_path / "merged_from_lora_hash.txt"

    if not os.path.exists(lora_weights):
        print(f"❌ LoRA weights not found for {model_key}: {lora_weights}")
        return

    lora_hash = hash_file(lora_weights)
    if hash_path.exists() and not force:
        with open(hash_path, "r") as f:
            if f.read().strip() == lora_hash:
                print(f"⏩ Skipping {model_key}: already merged from this LoRA. Use --force to override.")
                return

    print(f"\n🔧 Merging {model_key}...")
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=trust_remote_code
    )
    model = PeftModel.from_pretrained(model, lora_path)
    model = model.merge_and_unload()

    merged_path.mkdir(parents=True, exist_ok=True)

    # 💾 Save flat model.safetensors
    flat_path = merged_path / "model.safetensors"
    print(f"💾 Saving model to {flat_path}")
    state_dict = model.state_dict()

    # Fix shared weight issue
    if "lm_head.weight" in state_dict and "model.embed_tokens.weight" in state_dict:
        if state_dict["lm_head.weight"].data_ptr() == state_dict["model.embed_tokens.weight"].data_ptr():
            print("🛠️ Detected shared weights between lm_head and embed_tokens. Cloning lm_head.weight.")
            state_dict["lm_head.weight"] = state_dict["lm_head.weight"].clone()

    save_file(state_dict, str(flat_path), metadata={"format": "pt"})

    # 🧠 Save config and tokenizer
    model.config.save_pretrained(merged_path)
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=trust_remote_code)
    tokenizer.save_pretrained(merged_path)

    # 🔐 Save hash for future checks
    with open(hash_path, "w") as f:
        f.write(lora_hash)

    print(f"✅ Merged and saved: {merged_path}")

if __name__ == "__main__":
    # --- CLI Args ---
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_key", help="Merge one model from model_configs.json")
    parser.add_argument("--merge_all", action="store_true", help="Merge all models")
    parser.add_argument("--force", action="store_true", help="Force merge even if already done")
    args = parser.parse_args()

    # --- Determine which to merge ---
    if args.merge_all:
        keys_to_merge = list(all_configs.keys())
    elif args.model_key:
        keys_to_merge = [args.model_key]
    else:
        chosen_key, do_all = choose_model()
        keys_to_merge = list(all_configs.keys()) if do_all else [chosen_key]

    # --- Run merges ---
    for key in keys_to_merge:
        merge_lora_model(key, force=args.force)
