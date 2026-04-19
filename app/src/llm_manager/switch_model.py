#!/usr/bin/env python3
"""
Unified Smart-Assistant model switcher
---------------------------------------

• Intent models  →  directories that start with **lora_***
  - Symlink:  intent_active_model  (points at chosen lora_* dir)
  - PM2  :    llm_lora_intent  (restart after switch)

• Chat models    →  every other **model folder** that is *not* a symlink
  - Symlink:  chat_active_model   (points at chosen chat dir)
  - PM2  :    llm_chat            (rename or edit below if you use a different name)

Usage
-----

# interactive menu (prompt for intent/chat, then model list)
$ ./switch_model.py

# switch directly from CLI
$ ./switch_model.py --intent lora_llama3.2-3b
$ ./switch_model.py --chat   LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2
"""

import argparse, json, subprocess, sys
from pathlib import Path
from utils import (
    WEBUI_MODELS_DIR, PM2_INTENT, PM2_CHAT, load_configs, CONFIG_PATH,
)

# ── Paths ────────────────────────────────────────────────────────────────────
MODELS_DIR = WEBUI_MODELS_DIR
INTENT_LINK = MODELS_DIR / 'intent_active_model'
CHAT_LINK = MODELS_DIR / 'chat_active_model'

# PM2 process names
INTENT_PM2_PROCESS = PM2_INTENT
CHAT_PM2_PROCESS = PM2_CHAT


# ── Helpers ──────────────────────────────────────────────────────────────────
CFG = load_configs()

def resolve_display_name(model_dir: str) -> str:
    """
    Pretty name for menu:
      • For LoRA: strip 'lora_' and look up runtime.display_name.
      • For chat : look up full dir key; fall back to dir name.
    """
    base = model_dir.removeprefix("lora_")
    name = CFG.get(base, {}).get("runtime", {}).get("display_name")
    return name or base

def list_intent_models():
    return sorted(p.name for p in MODELS_DIR.iterdir()
                  if p.is_dir())

def list_chat_models():
    ignore = {"intent_active_model", "chat_active_model"}
    return sorted(p.name for p in MODELS_DIR.iterdir()
                  if p.is_dir() and not p.name.startswith("lora_") and p.name not in ignore)

def make_symlink(link_path: Path, target_dir: Path):
    if link_path.is_symlink() or link_path.exists():
        link_path.unlink()
    link_path.symlink_to(target_dir, target_is_directory=True)

def switch(model_dir: str, mode: str):
    """
    mode = "intent" or "chat"
    """
    target = MODELS_DIR / model_dir
    if not target.exists():
        print(f"❌ Model directory not found: {target}")
        sys.exit(1)

    link = INTENT_LINK if mode == "intent" else CHAT_LINK
    make_symlink(link, target)
    print(f"✅ Switched {mode} model → {model_dir}")

    pm2_process = INTENT_PM2_PROCESS if mode == "intent" else CHAT_PM2_PROCESS
    if pm2_process:
        print(f"🔄 Restarting PM2 process '{pm2_process}' …")
        subprocess.run(["pm2", "restart", pm2_process])
    else:
        print("ℹ️  No PM2 process configured for this mode; restart manually.")

# ── CLI / Menu ───────────────────────────────────────────────────────────────
def choose(models, title):
    print(f"\n{title}")
    for idx, m in enumerate(models, 1):
        print(f" {idx}. {resolve_display_name(m)}  ({m})")
    try:
        choice = int(input("\nSelect a model: ")) - 1
        if 0 <= choice < len(models):
            return models[choice]
    except ValueError:
        pass
    print("❌ Invalid choice.")
    return None

def interactive():
    while True:
        print("\n=== Model Switcher ===")
        print(" 1. Switch INTENT model (LoRA)")
        print(" 2. Switch CHAT   model")
        print(" 3. Quit")
        sel = input("Choose an option: ").strip()
        if sel == "1":
            models = list_intent_models()
            if not models:
                print("⚠️  No LoRA models found.")
                continue
            chosen = choose(models, "🧠 Available LoRA Intent Models:")
            if chosen:
                switch(chosen, "intent")
        elif sel == "2":
            models = list_chat_models()
            if not models:
                print("⚠️  No chat models found.")
                continue
            chosen = choose(models, "🤖 Available Chat Models:")
            if chosen:
                switch(chosen, "chat")
        elif sel == "3":
            break

def main():
    ap = argparse.ArgumentParser(description="Switch Smart-Assistant LLMs")
    ap.add_argument("--intent", metavar="DIR", help="switch intent model directly")
    ap.add_argument("--chat",   metavar="DIR", help="switch chat   model directly")
    args = ap.parse_args()

    if args.intent and args.chat:
        ap.error("choose either --intent or --chat, not both")

    if args.intent:
        switch(args.intent, "intent")
    elif args.chat:
        switch(args.chat, "chat")
    else:
        interactive()

if __name__ == "__main__":
    main()
