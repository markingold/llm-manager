#!/usr/bin/env python3
"""
Smart-Assistant LoRA / Model Manager
────────────────────────────────────
• Train LoRA (single- or dual-GPU)              →  train_lora.py / train_lora_dual.py
• Merge, convert to EXL2, copy to textgen-webui
• Serve an EXL2 model
• Re-build combined dataset
• *NEW*  Switch active INTENT (LoRA) or CHAT model with symlinks & PM2.

CLI quick-start
───────────────
# run full pipeline with dual-GPU trainer
$ python main.py --pipeline --model_key llama3.1-8b --dual

# switch intent model non-interactively
$ python main.py --switch-intent lora_llama3.2-3b
"""

import os, sys, json, shutil, subprocess, argparse
from pathlib import Path

# ── Local modules ───────────────────────────────────────────────────
from train_lora       import train_model   as train_model_single
from train_lora_dual  import train_model   as train_model_dual
from train_lora       import build_combined_dataset
from merge_lora       import merge_lora_model
from convert_lora     import convert_to_exl2

# ── Paths / Config ──────────────────────────────────────────────────
CONFIG_PATH   = Path("model_configs.json")
TEXTGEN_DIR = TEXTGEN_DIR
EXL2_DEST_DIR = EXL2_DEST_DIR

INTENT_LINK   = EXL2_DEST_DIR / "intent_active_model"
CHAT_LINK     = EXL2_DEST_DIR / "chat_active_model"
INTENT_PM2_PROCESS = INTENT_PM2_PROCESS
CHAT_PM2_PROCESS = CHAT_PM2_PROCESS

with CONFIG_PATH.open() as f:
    all_configs: dict[str, dict] = json.load(f)

# ────────────────────────────────────────────────────────────────────
# Utility helpers
# ────────────────────────────────────────────────────────────────────
def has_lora_weights(k):   return Path(f"output/intent_{k}/adapter_model.safetensors").exists()
def has_merged_model(k):   return Path(f"output/merged_{k}/model.safetensors").exists()
def has_converted_exl2(k): return Path(f"output/lora_{k}").is_dir()

def select_model(cands: list[str], label="Select model") -> list[str]:
    print(f"\n{label}:")
    for i, k in enumerate(cands, 1):
        print(f"  {i}. {k}")
    print(f"  {len(cands)+1}. 🔁 All")
    while True:
        try:
            c = int(input("Choice: "))
            if 1 <= c <= len(cands):   return [cands[c-1]]
            if c == len(cands)+1:      return cands
        except ValueError: pass
        print("❌ Invalid selection.")

# ── Training-mode selector (single / dual) ──────────────────────────
def choose_training_mode(force: bool|None = None):
    """
    Returns (train_fn, mode_string)
    force=True  → dual,  force=False → single, force=None → prompt
    """
    if force is not None:
        return (train_model_dual, "Dual-GPU") if force else (train_model_single, "Single-GPU")

    print("\n🖥️  Choose training mode:")
    print("  1. Single-GPU")
    print("  2. Dual-GPU  (DDP, GPUs 0 & 1)")
    while True:
        sel = input("Choice (1/2): ").strip()
        if sel == "1": return train_model_single, "Single-GPU"
        if sel == "2": return train_model_dual,  "Dual-GPU"
        print("❌ Invalid choice.")

# ────────────────────────────────────────────────────────────────────
#  Model-switcher functions (merged from switch_model.py)
# ────────────────────────────────────────────────────────────────────
def _cfg_display_name(dir_name: str) -> str:
    key = dir_name.removeprefix("lora_")
    return all_configs.get(key, {}).get("runtime", {}).get("display_name", key)

def _list_intent_models() -> list[str]:
    return sorted(p.name for p in EXL2_DEST_DIR.iterdir()
                  if p.is_dir() and p.name.startswith("lora_"))

def _list_chat_models() -> list[str]:
    ignore = {"intent_active_model", "chat_active_model"}
    return sorted(p.name for p in EXL2_DEST_DIR.iterdir()
                  if p.is_dir() and p.name not in ignore and not p.name.startswith("lora_"))

def _make_symlink(link: Path, target: Path):
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target, target_is_directory=True)

def _switch_model(model_dir: str, mode: str):
    target = EXL2_DEST_DIR / model_dir
    if not target.exists():
        print(f"❌ Model dir not found: {target}")
        sys.exit(1)

    link  = INTENT_LINK if mode == "intent" else CHAT_LINK
    _make_symlink(link, target)
    print(f"✅ Switched {mode.upper()} model → {model_dir}")

    pm2   = INTENT_PM2_PROCESS if mode == "intent" else CHAT_PM2_PROCESS
    if pm2:
        print(f"🔄 Restarting PM2 process '{pm2}' …")
        subprocess.run(["pm2", "restart", pm2], check=False)

def _interactive_switch():
    while True:
        print("\n=== Model Switcher ===")
        print(" 1. Switch INTENT model (LoRA)")
        print(" 2. Switch CHAT   model")
        print(" 3. ← Back to main menu")
        sel = input("Choose an option: ").strip()
        if sel == "1":
            mods = _list_intent_models()
            if not mods:
                print("⚠️  No LoRA models found.")
                continue
            for i, m in enumerate(mods, 1):
                print(f" {i}. {_cfg_display_name(m)} ({m})")
            try:
                idx = int(input("Select model: ")) - 1
                if 0 <= idx < len(mods):
                    _switch_model(mods[idx], "intent")
            except ValueError:
                print("❌ Invalid choice.")
        elif sel == "2":
            mods = _list_chat_models()
            if not mods:
                print("⚠️  No chat models found.")
                continue
            for i, m in enumerate(mods, 1):
                print(f" {i}. {_cfg_display_name(m)} ({m})")
            try:
                idx = int(input("Select model: ")) - 1
                if 0 <= idx < len(mods):
                    _switch_model(mods[idx], "chat")
            except ValueError:
                print("❌ Invalid choice.")
        elif sel == "3":
            break

# ────────────────────────────────────────────────────────────────────
#  Core pipeline helpers
# ────────────────────────────────────────────────────────────────────
def _copy_exl2_to_textgen(k):
    src, dst = Path(f"output/lora_{k}"), EXL2_DEST_DIR / f"lora_{k}"
    if not src.exists(): return False
    if dst.exists(): shutil.rmtree(dst)
    shutil.copytree(src, dst); return True

def _cleanup_output(k):
    for p in [f"output/intent_{k}", f"output/merged_{k}", f"output/lora_{k}"]:
        shutil.rmtree(p, ignore_errors=True)

def train_models(train_fn):
    data = build_combined_dataset()
    for k in select_model(list(all_configs.keys()), "📚 Choose model to train"):
        train_fn(k, data)

def merge_models():
    for k in select_model([m for m in all_configs if has_lora_weights(m)], "🧬 Choose model to merge"):
        merge_lora_model(k)

def convert_models():
    for k in select_model([m for m in all_configs if has_merged_model(m)], "🔁 Choose model to convert"):
        convert_to_exl2(k)

def serve_model():
    avail = [m for m in all_configs if has_converted_exl2(m)]
    for k in select_model(avail, "🚀 Choose EXL2 model to serve"):
        subprocess.Popen([
            "python3", "server.py", "--model", f"{k}-exl2",
            "--loader", "exllamav2", "--nowebui",
            "--api", "--listen", "--api-port", "5501", "--max_seq_len", "8192"
        ], cwd=str(TEXTGEN_DIR))
        print(f"✅ Serving {k}-exl2 on port 5501")

def copy_to_textgen():
    for k in select_model([m for m in all_configs if has_converted_exl2(m)],
                          "📁 Choose model to copy"):
        _copy_exl2_to_textgen(k)
        print(f"✅ Copied lora_{k} to textgen-webui")

def pipeline(models, train_fn):
    data = build_combined_dataset()
    for k in models:
        train_fn(k, data); merge_lora_model(k)
        convert_to_exl2(k)
        if _copy_exl2_to_textgen(k): _cleanup_output(k)

# ────────────────────────────────────────────────────────────────────
#  Interactive menu
# ────────────────────────────────────────────────────────────────────
def main_menu():
    print("\n🧠 Smart LoRA Assistant Menu")
    print("1. 📚 Train a Model")
    print("2. 🧬 Merge LoRA to Base")
    print("3. 🔁 Convert Merged to EXL2")
    print("4. 🚀 Serve EXL2 Model")
    print("5. 📁 Copy EXL2 to text-generation-webui")
    print("6. 🧱 Rebuild Dataset")
    print("7. 🛠️  Full Pipeline (train→copy→cleanup)")
    print("8. 💾 List Models & Status")
    print("9. 🔄 Switch Active Model")
    print("0. 🚪 Exit")
    return input("Choose an option: ").strip()

def list_status():
    print("\n📊 Model Status:")
    for k in all_configs:
        print(f"- {k:20}  LoRA:{'✅' if has_lora_weights(k) else '✗'}  "
              f"Merged:{'✅' if has_merged_model(k) else '✗'}  "
              f"EXL2:{'✅' if has_converted_exl2(k) else '✗'}")

# ────────────────────────────────────────────────────────────────────
#  CLI parsing
# ────────────────────────────────────────────────────────────────────
cli = argparse.ArgumentParser(description="Smart LoRA Manager + Switcher")
cli.add_argument("--pipeline", action="store_true",
                 help="run full pipeline (needs --model_key or --all)")
cli.add_argument("--model_key")
cli.add_argument("--all", action="store_true")

cli.add_argument("--dual",   action="store_true", help="force dual-GPU trainer")
cli.add_argument("--single", action="store_true", help="force single-GPU trainer")

cli.add_argument("--switch-intent")
cli.add_argument("--switch-chat")

args, _ = cli.parse_known_args()

# Decide training backend once
train_fn, mode_str = choose_training_mode(
    False if args.single else True if args.dual else None)
if not args.pipeline:
    print(f"⚙️  Trainer backend: {mode_str}")

# ────────────────────────────────────────────────────────────────────
#  Entry point
# ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    # --- direct model switch without menu --------------------------
    if args.switch_intent:
        _switch_model(args.switch_intent, "intent"); sys.exit()
    if args.switch_chat:
        _switch_model(args.switch_chat,   "chat");   sys.exit()

    # --- non-interactive pipeline ---------------------------------
    if args.pipeline:
        keys = list(all_configs) if args.all else [args.model_key]
        if not keys or None in keys:
            cli.error("Specify --model_key <name> or --all with --pipeline")
        pipeline(keys, train_fn); sys.exit()

    # --- interactive menu -----------------------------------------
    while True:
        match main_menu():
            case "1": train_models(train_fn)
            case "2": merge_models()
            case "3": convert_models()
            case "4": serve_model()
            case "5": copy_to_textgen()
            case "6": build_combined_dataset()
            case "7":
                ks = select_model(list(all_configs), "🛠️  Choose models for pipeline")
                pipeline(ks, train_fn)
            case "8": list_status()
            case "9": _interactive_switch()
            case "0": print("👋 Bye!"); break
            case _:  print("❌ Invalid choice.")
