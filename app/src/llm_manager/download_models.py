#!/usr/bin/env python3
"""
Download Hugging Face models in two ways:

1.  Config-based (unchanged):
      • --model_key llama3_8b
      • --download_all

2.  One-off direct download into text-generation-webui:
      • --custom_model TheMelonGod/Qwen3-14B-exl2
      • optional: --target_dir /path/to/text-generation-webui/user_data/models
"""

import os, json, argparse, sys
from pathlib import Path
from dotenv import load_dotenv
from huggingface_hub import snapshot_download            # for one-off downloads
from transformers import AutoTokenizer, AutoModelForCausalLM  # for cache-only flow

# ──────────────────────────────────────────────────────────────────────────────
# 🔑  Auth
# ──────────────────────────────────────────────────────────────────────────────
load_dotenv('secrets/.env')
HF_TOKEN = os.getenv("HF_TOKEN")       # leave blank for public models

# ──────────────────────────────────────────────────────────────────────────────
# 📖  Load config file (only needed for old workflow)
# ──────────────────────────────────────────────────────────────────────────────
CONFIG_PATH = Path("model_configs.json")
all_configs = {}
if CONFIG_PATH.exists():
    with CONFIG_PATH.open() as f:
        all_configs = json.load(f)

# ──────────────────────────────────────────────────────────────────────────────
# 🛠️  CLI
# ──────────────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
g = parser.add_mutually_exclusive_group(required=False)
g.add_argument("--model_key", help="Download a model listed in model_configs.json")
g.add_argument("--download_all", action="store_true", help="Download every model in model_configs.json")
g.add_argument("--custom_model", help="HF repo to download directly into text-generation-webui")

parser.add_argument("--target_dir",
                    default=os.getenv("WEBUI_MODELS_DIR", "text-generation-webui/user_data/models"),
                    help="Destination dir for --custom_model (default: %(default)s)")

parser.add_argument("--trust_remote_code", action="store_true",
                    help="Set if your --custom_model repo needs custom code")

args = parser.parse_args()

# ──────────────────────────────────────────────────────────────────────────────
# 🚚  Helper for direct downloads
# ──────────────────────────────────────────────────────────────────────────────
def download_to_webui(repo_id: str, target_dir: Path, trust_remote: bool):
    # text-generation-webui expects one folder per model; use a safe name
    safe_name = repo_id.replace("/", "__")
    local_path = target_dir.expanduser() / safe_name
    print(f"\n⬇️  Downloading {repo_id} → {local_path}")

    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=local_path,
            token=HF_TOKEN,
            local_dir_use_symlinks=False,   # real files instead of symlinks
            allow_patterns=None,            # grab the whole repo
            ignore_patterns=["*.safetensors.index.json"],  # optional: skip index shards
        )
        print(f"✅ Finished: {repo_id}")
        if trust_remote:
            # Just a reminder; nothing else to do at download time
            print("⚠️  Remember to launch text-generation-webui with --trust-remote-code for this model.")
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)

# ──────────────────────────────────────────────────────────────────────────────
# 🏁  Main execution paths
# ──────────────────────────────────────────────────────────────────────────────
if args.custom_model:
    download_to_webui(args.custom_model, Path(args.target_dir), args.trust_remote_code)
    sys.exit(0)

# If we get here we’re using the old config-based flow
if not all_configs:
    print("❌ model_configs.json not found or empty, and --custom_model not supplied.")
    sys.exit(1)

if args.download_all:
    keys_to_download = list(all_configs.keys())
elif args.model_key:
    if args.model_key not in all_configs:
        print(f"❌ '{args.model_key}' not in {CONFIG_PATH}")
        sys.exit(1)
    keys_to_download = [args.model_key]
else:
    # Interactive fallback: let user paste a repo ID when no flags are given
    print("No flags supplied.")
    repo_id = input("Paste a HF repo ID to download (or leave blank to abort): ").strip()
    if repo_id:
        download_to_webui(repo_id, Path(args.target_dir), args.trust_remote_code)
    else:
        print("👋 Nothing to do.")
    sys.exit(0)

# ──────────────────────────────────────────────────────────────────────────────
# ♻️  Config-based download into HF cache (unchanged behaviour)
# ──────────────────────────────────────────────────────────────────────────────
for key in keys_to_download:
    cfg = all_configs[key]
    model_name = cfg["model_name"]
    trust_remote_code = cfg.get("trust_remote_code", False)

    print(f"\n⬇️  Caching: {key} ({model_name})")
    try:
        AutoTokenizer.from_pretrained(
            model_name,
            trust_remote_code=trust_remote_code,
            token=HF_TOKEN
        )
        AutoModelForCausalLM.from_pretrained(
            model_name,
            trust_remote_code=trust_remote_code,
            token=HF_TOKEN
        )
        print(f"✅ Cached: {key}")
    except Exception as e:
        print(f"❌ Failed to cache {model_name}: {e}")
