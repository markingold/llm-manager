import os

# GPU selection: honour env (.env → systemd → caller), default to GPU 1
from utils import CUDA_DEVICES as _CUDA, hash_file, choose_model as _choose_model, load_configs, build_combined_dataset
os.environ.setdefault("CUDA_VISIBLE_DEVICES", _CUDA)

import json
import argparse
import torch
import glob
import random
import gc
from pathlib import Path
from datetime import datetime
from datasets import load_dataset
from transformers import (
    AutoTokenizer, AutoModelForCausalLM,
    TrainingArguments, Trainer, DataCollatorForLanguageModeling
)
from peft import LoraConfig, get_peft_model, TaskType
from dotenv import load_dotenv

# ──  TRAINING HYPER-PARAMS  ──────────────────────────────────────────
BATCH            = 3         # per-GPU micro-batch
GRAD_ACCUM       = 4         # gradient accumulation steps
EPOCHS           = 3         # total epochs
LR               = 5e-5       # learning rate
FP16             = True      # fp16 if supported
BF16             = False
OPTIM            = "paged_adamw_8bit"
WARMUP_STEPS     = 20
SAVE_EVERY_STEPS = 100
LOG_EVERY_STEPS  = 10
MAX_SEQ_LEN      = 192       # must match config["max_length"]

# Disable wandb
os.environ["WANDB_DISABLED"] = "true"
FLASH_BLOCKLIST = {"Qwen/Qwen3-4B"}

# Load Hugging Face token
load_dotenv()
HF_TOKEN = os.getenv("HF_TOKEN")

# Load model configurations
all_configs = load_configs()

# Re-export choose_model with a sensible label
def choose_model():
    return _choose_model(all_configs, "Models to Train")

# --- Tokenization for canonical data ---
def get_tokenized_dataset(data_path, tokenizer, max_length=256):
    dataset = load_dataset("json", data_files={"train": str(data_path)})["train"]

    def tokenize(batch):
        texts = [
            tokenizer.apply_chat_template(
                [
                    {"role": "user", "content": p.strip()},
                    {"role": "assistant", "content": r.strip()},
                ],
                tokenize=False,
                add_generation_prompt=False
            )
            for p, r in zip(batch["prompt"], batch["response"])
        ]
        tokenized = tokenizer(
            texts,
            truncation=True,
            padding="max_length",
            max_length=max_length
        )
        if not tokenized.get("input_ids") or any(len(ids)==0 for ids in tokenized["input_ids"]):
            raise ValueError("Tokeniser returned empty input_ids.")
        tokenized["labels"] = tokenized["input_ids"].copy()
        return tokenized

    dataset = dataset.map(
        tokenize,
        batched=True,
        num_proc=os.cpu_count()//2 or 1,
        remove_columns=dataset.column_names
    )
    dataset = dataset.shuffle(seed=42)
    dataset.set_format(type="torch", columns=["input_ids","attention_mask","labels"])
    return dataset

# --- Training Runner ---
def train_model(model_key, data_path, force=False):
    cfg = all_configs[model_key]
    model_name = cfg["model_name"]
    output_dir = f"./output/intent_{model_key}"
    os.makedirs(output_dir, exist_ok=True)

    data_hash = hash_file(data_path)
    hash_file_path = os.path.join(output_dir, "dataset_hash.txt")
    if os.path.exists(hash_file_path) and not force:
        with open(hash_file_path) as hf:
            if hf.read().strip() == data_hash:
                print(f"⏩ Skipping {model_key}: already trained.")
                return

    print(f"\n🚀 Training '{model_key}'...")
    tokenizer = AutoTokenizer.from_pretrained(
        model_name,
        trust_remote_code=cfg.get("trust_remote_code", False),
        token=HF_TOKEN
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype="auto",
        trust_remote_code=cfg.get("trust_remote_code", False),
        attn_implementation=("flash_attention_2" if all(b not in model_name for b in FLASH_BLOCKLIST) else "torch"),
        token=HF_TOKEN,
    )
    model = get_peft_model(model, LoraConfig(
        r=cfg.get("r", 16),
        lora_alpha=cfg.get("lora_alpha", 32),
        target_modules=cfg["target_modules"],
        lora_dropout=cfg.get("lora_dropout", 0.05),
        bias=cfg.get("bias", "none"),
        task_type=TaskType.CAUSAL_LM
    ))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)

    dataset = get_tokenized_dataset(data_path, tokenizer, MAX_SEQ_LEN)

    args = TrainingArguments(
        output_dir=output_dir,
        per_device_train_batch_size=BATCH,
        gradient_accumulation_steps=GRAD_ACCUM,
        num_train_epochs=EPOCHS,
        learning_rate=LR,
        warmup_steps=WARMUP_STEPS,
        fp16=FP16,
        optim=OPTIM,
        logging_steps=LOG_EVERY_STEPS,
        save_steps=SAVE_EVERY_STEPS,
        save_total_limit=2,
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=dataset,
        tokenizer=tokenizer,
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
    )

    print(f"🧪 Samples: {len(dataset)}, batch={args.per_device_train_batch_size}x{args.gradient_accumulation_steps}")
    trainer.train()

    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    with open(os.path.join(output_dir, "generation_config.json"), "w") as gcfg:
        json.dump(cfg.get("generation_config", {}), gcfg, indent=2)
    with open(hash_file_path, "w") as hf:
        hf.write(data_hash)

    del trainer, model
    torch.cuda.empty_cache()
    gc.collect()

# --- CLI Entrypoint ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_key", help="Train one model from config")
    parser.add_argument("--train_all", action="store_true", help="Train all models")
    parser.add_argument("--force", action="store_true", help="Retrain even if hash matches")
    parser.add_argument("--data_path", help="Path to canonical JSONL dataset")
    args = parser.parse_args()

    if not args.data_path:
        data_file = build_combined_dataset()
    else:
        data_file = args.data_path
    print(f"📄 Using canonical dataset → {data_file}")

    if args.train_all:
        to_train = list(all_configs.keys())
    elif args.model_key:
        to_train = [args.model_key]
    else:
        sel, all_flag = choose_model()
        to_train = list(all_configs.keys()) if all_flag else [sel]

    for key in to_train:
        train_model(key, data_file, force=args.force)
