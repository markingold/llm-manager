#!/usr/bin/env python3
import os, json, subprocess
from pathlib import Path

CONFIG_PATH = Path('model_configs.json')
MODELS_DIR = MODELS_DIR
SYMLINK_PATH = MODELS_DIR / "intent_active_model"

def load_cfg():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def list_lora_models():
    return sorted(p.name for p in MODELS_DIR.iterdir()
                  if p.is_dir() and p.name.startswith("lora_"))

def resolve_display_name(lora_key: str) -> str:
    base = lora_key.removeprefix("lora_")
    cfg  = load_cfg().get(base, {})
    return cfg.get("runtime", {}).get("display_name", base)

def switch_model(lora_key: str):
    new_path = MODELS_DIR / lora_key
    if not new_path.exists():
        print(f"❌ Model path not found: {new_path}")
        return

    if SYMLINK_PATH.is_symlink() or SYMLINK_PATH.exists():
        SYMLINK_PATH.unlink()

    SYMLINK_PATH.symlink_to(new_path, target_is_directory=True)
    print(f"✅ Switched intent model to '{lora_key}'")

    print("🔄 Restarting intent model server (pm2 process 'llm_lora_intent')...")
    subprocess.run(["pm2", "restart", PM2_INTENT])

def show_menu_and_get_choice(models):
    print("\n🧠 Available LoRA Models:")
    for idx, m in enumerate(models, 1):
        print(f" {idx}. {resolve_display_name(m)}  ({m})")
    try:
        choice = int(input("\nSelect a model to activate: ")) - 1
        if 0 <= choice < len(models):
            return models[choice]
    except ValueError:
        pass
    print("❌ Invalid choice.")
    return None

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        switch_model(sys.argv[1])
    else:
        available = list_lora_models()
        if not available:
            print("❌ No LoRA models found in models folder.")
        else:
            chosen = show_menu_and_get_choice(available)
            if chosen:
                switch_model(chosen)
