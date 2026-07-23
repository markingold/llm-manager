#!/usr/bin/env python3
"""Legacy LoRA training/conversion menu; serving is owned by llm-manager API."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from convert_lora import convert_to_exl2
from merge_lora import merge_lora_model
from train_lora import train_model as train_model_single
from train_lora_dual import train_model as train_model_dual
from utils import WEBUI_MODELS_DIR, build_combined_dataset, load_configs

EXL2_DEST_DIR = WEBUI_MODELS_DIR
all_configs = load_configs()


def has_lora_weights(key: str) -> bool:
    return Path(f"output/intent_{key}/adapter_model.safetensors").exists()


def has_merged_model(key: str) -> bool:
    return Path(f"output/merged_{key}/model.safetensors").exists()


def has_converted_exl2(key: str) -> bool:
    return Path(f"output/lora_{key}").is_dir()


def select_model(candidates: list[str], label: str = "Select model") -> list[str]:
    print(f"\n{label}:")
    for index, key in enumerate(candidates, 1):
        print(f"  {index}. {key}")
    print(f"  {len(candidates) + 1}. All")
    while True:
        try:
            choice = int(input("Choice: "))
        except ValueError:
            print("Invalid selection.")
            continue
        if 1 <= choice <= len(candidates):
            return [candidates[choice - 1]]
        if choice == len(candidates) + 1:
            return candidates
        print("Invalid selection.")


def choose_training_mode(force: bool | None = None):
    if force is not None:
        return (train_model_dual, "Dual-GPU") if force else (train_model_single, "Single-GPU")
    while True:
        choice = input("Training mode: 1) single GPU  2) dual GPU: ").strip()
        if choice == "1":
            return train_model_single, "Single-GPU"
        if choice == "2":
            return train_model_dual, "Dual-GPU"


def _copy_exl2_to_textgen(key: str) -> bool:
    source = Path(f"output/lora_{key}")
    destination = EXL2_DEST_DIR / f"lora_{key}"
    if not source.exists():
        return False
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return True


def _cleanup_output(key: str) -> None:
    for path in (f"output/intent_{key}", f"output/merged_{key}", f"output/lora_{key}"):
        shutil.rmtree(path, ignore_errors=True)


def pipeline(models: list[str], train_fn) -> None:
    data = build_combined_dataset()
    for key in models:
        train_fn(key, data)
        merge_lora_model(key)
        convert_to_exl2(key)
        if _copy_exl2_to_textgen(key):
            _cleanup_output(key)


def list_status() -> None:
    for key in all_configs:
        print(
            f"- {key:20}  LoRA:{'yes' if has_lora_weights(key) else 'no'}  "
            f"Merged:{'yes' if has_merged_model(key) else 'no'}  "
            f"EXL2:{'yes' if has_converted_exl2(key) else 'no'}"
        )


def interactive(train_fn) -> None:
    while True:
        print("\nLoRA training and conversion")
        print("1. Train  2. Merge  3. Convert  4. Copy  5. Rebuild data  6. Full pipeline  7. Status  0. Exit")
        choice = input("Choice: ").strip()
        if choice == "1":
            data = build_combined_dataset()
            for key in select_model(list(all_configs), "Choose model to train"):
                train_fn(key, data)
        elif choice == "2":
            for key in select_model([row for row in all_configs if has_lora_weights(row)], "Choose model to merge"):
                merge_lora_model(key)
        elif choice == "3":
            for key in select_model([row for row in all_configs if has_merged_model(row)], "Choose model to convert"):
                convert_to_exl2(key)
        elif choice == "4":
            for key in select_model([row for row in all_configs if has_converted_exl2(row)], "Choose model to copy"):
                _copy_exl2_to_textgen(key)
        elif choice == "5":
            build_combined_dataset()
        elif choice == "6":
            pipeline(select_model(list(all_configs), "Choose models for pipeline"), train_fn)
        elif choice == "7":
            list_status()
        elif choice == "0":
            return


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LoRA training/conversion only; use POST /models/load for serving and model switches"
    )
    parser.add_argument("--pipeline", action="store_true", help="run full pipeline (requires --model-key or --all)")
    parser.add_argument("--model-key", "--model_key", dest="model_key")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dual", action="store_true", help="force dual-GPU trainer")
    parser.add_argument("--single", action="store_true", help="force single-GPU trainer")
    args = parser.parse_args()
    if args.dual and args.single:
        parser.error("choose only one of --dual or --single")
    train_fn, _ = choose_training_mode(False if args.single else True if args.dual else None)
    if args.pipeline:
        keys = list(all_configs) if args.all else [args.model_key]
        if not keys or None in keys:
            parser.error("specify --model-key <name> or --all with --pipeline")
        pipeline(keys, train_fn)
        return
    interactive(train_fn)


if __name__ == "__main__":
    main()
