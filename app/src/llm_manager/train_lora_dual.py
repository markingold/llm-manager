#!/usr/bin/env python
"""
train_lora_dual.py – two-GPU LoRA trainer via 🤗 Accelerate.

Usage:
  # Single-GPU:
  python train_lora_dual.py --model_key llama3.2-3b

  # Multi-GPU (2 GPUs):
  accelerate launch --num_processes 2 train_lora_dual.py --train_all
"""
import os
import sys
import json
import gc
import argparse
from pathlib import Path
from datetime import datetime, timedelta

from dotenv import load_dotenv
import torch
import torch.distributed as dist
from datasets import load_dataset
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    Trainer,
    TrainingArguments,
    DataCollatorForLanguageModeling
)
from peft import LoraConfig, get_peft_model, TaskType
from utils import hash_file, choose_model, build_combined_dataset, load_configs

# ── Hyper-params ────────────────────────────────────────────────────────────
BATCH            = 3
GRAD_ACCUM       = 4
EPOCHS           = 3
LR               = 5e-5
FP16             = True
OPTIM            = "paged_adamw_8bit"
WARMUP_STEPS     = 20
SAVE_EVERY_STEPS = 100
LOG_EVERY_STEPS  = 10
MAX_SEQ_LEN      = 192
FLASH_BLOCKLIST  = {"Qwen/Qwen3-4B"}

# ── Load config & env ────────────────────────────────────────────────────────
load_dotenv()
HF_TOKEN = os.getenv("HF_TOKEN")
all_configs = load_configs()

# ── Distributed setup ────────────────────────────────────────────────────────
local_rank = int(os.getenv("LOCAL_RANK", 0))
world_size = int(os.getenv("WORLD_SIZE", 1))
multi_gpu  = world_size > 1

# ── Utility functions ───────────────────────────────────────────────────────
# hash_file, build_combined_dataset, choose_model are now in utils.py

def tokenize_dataset(path: str, tok: AutoTokenizer):
    ds = load_dataset("json", data_files={"train": path})["train"]
    def _tok(batch):
        texts = [f"User: {u.strip()}\nAssistant: {r.strip()}"
                 for u, r in zip(batch["prompt"], batch["response"])]
        out = tok(texts, truncation=True, padding="max_length", max_length=MAX_SEQ_LEN)
        out["labels"] = out["input_ids"].copy()
        return out

    ds = ds.map(
        _tok,
        batched=True,
        num_proc=max(os.cpu_count() // 2, 1),
        remove_columns=ds.column_names
    )
    ds.set_format(type="torch", columns=["input_ids","attention_mask","labels"])
    return ds

def train_model(key: str, data_path: str, force: bool = False):
    cfg = all_configs[key]
    model_name = cfg["model_name"]
    out_dir = f"./output/intent_{key}"
    os.makedirs(out_dir, exist_ok=True)

    # tokenizer + base model
    tok = AutoTokenizer.from_pretrained(model_name, trust_remote_code=cfg.get("trust_remote_code", False), token=HF_TOKEN)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    base = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype="auto",
        trust_remote_code=cfg.get("trust_remote_code", False),
        attn_implementation=("flash_attention_2"
            if all(b not in model_name for b in FLASH_BLOCKLIST)
            else "torch"),
        token=HF_TOKEN
    )

    # apply LoRA
    peft_model = get_peft_model(
        base,
        LoraConfig(
            r=cfg.get("r",16),
            lora_alpha=cfg.get("lora_alpha",32),
            target_modules=cfg["target_modules"],
            lora_dropout=cfg.get("lora_dropout",0.05),
            bias=cfg.get("bias","none"),
            task_type=TaskType.CAUSAL_LM
        )
    )
    peft_model.enable_input_require_grads()
    peft_model.to(f"cuda:{local_rank}" if torch.cuda.is_available() else "cpu")

    # dataset
    ds = tokenize_dataset(data_path, tok)

    # training args
    args = TrainingArguments(
        output_dir=out_dir,
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
        ddp_find_unused_parameters=False if multi_gpu else None,
        ddp_backend="nccl" if multi_gpu else None
    )

    trainer = Trainer(
        model=peft_model,
        args=args,
        train_dataset=ds,
        tokenizer=tok,
        data_collator=DataCollatorForLanguageModeling(tok, mlm=False)
    )

    if local_rank == 0:
        print(f"🧪 Training {key} → {len(ds)} samples")

    trainer.train()

    # save only on rank 0
    if local_rank == 0:
        peft_model.save_pretrained(out_dir)
        tok.save_pretrained(out_dir)

    torch.cuda.empty_cache()
    gc.collect()

# ── Main CLI / dispatch ───────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train LoRA with single or multi-GPU")
    parser.add_argument("--model_key", help="Train one model from config")
    parser.add_argument("--train_all", action="store_true", help="Train all models")
    parser.add_argument("--data_path", help="Path to JSONL dataset")
    args = parser.parse_args()

    # init DDP only if >1 GPU
    if multi_gpu:
        dist.init_process_group(backend="nccl", init_method="env://", timeout=timedelta(minutes=30))
        torch.cuda.set_device(local_rank)

    # build or load dataset
    data_file = args.data_path or build_combined_dataset()

    # choose which models
    if args.train_all:
        to_train = list(all_configs.keys())
    elif args.model_key:
        to_train = [args.model_key]
    else:
        if local_rank == 0:
            sel, all_flag = choose_model()
            to_train = list(all_configs.keys()) if all_flag else [sel]
        else:
            to_train = None
        # broadcast from rank 0 → all
        obj = [to_train]
        dist.broadcast_object_list(obj, src=0)
        to_train = obj[0]

    # launch
    for key in to_train:
        if local_rank == 0:
            print(f"\n=== {key.upper()} (rank {local_rank}/{world_size}) ===")
        train_model(key, data_file)

    if multi_gpu:
        dist.destroy_process_group()
