# LLM Manager

Production control plane for multi-slot LLM serving on dual-GPU systems.  
Manages **text-generation-webui** instances via systemd, with model switching, format auto-detection, LoRA training pipeline, and a web dashboard.

## Architecture

```
  ┌──────────────────────────────────────────────────────────────┐
  │  Web Dashboard (index.html + app.js)                        │
  │  → Apache reverse-proxy /llm-manager-api → :8101            │
  └───────┬──────────────────────────────────────────────────────┘
          │
  ┌───────▼──────────────────────────────────────────────────────┐
  │  FastAPI  server.py (:8101)                                  │
  │  • /models, /switch, /bounce, /inspect, /vram               │
  │  • /engines/status, /engines/{mode}/{start|stop|restart}     │
  │  • /jobs (train, merge, convert)                             │
  │  • /health, /system, /knobs, /test-*                        │
  └───────┬──────────────────────────────────────────────────────┘
          │ systemctl / symlinks
  ┌───────▼──────────┐  ┌──────────────┐  ┌──────────────┐
  │ llm-a.service    │  │ llm-b.service│  │ llm-c.service│
  │ GPU 0 · :8500   │  │ GPU 1 · :8501│  │ GPU 1 · :8502│
  │ chat slot        │  │ intent slot  │  │ small slot   │
  └──────────────────┘  └──────────────┘  └──────────────┘
        text-generation-webui (exllamav2 loader)
```

**Hardware:** 2× NVIDIA RTX 3090 (24 GB each)

## Quick Start

```bash
# 1. Clone / setup
cd /srv/2bananas/projects/llm-manager
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Create config
cp config/settings.example.env secrets/.env
# Edit secrets/.env with your paths/tokens

# 3. Start the API
python api/server.py
# (or use the systemd unit: sudo systemctl start llm-manager-api)

# 4. Open dashboard
# http://<host>/llm-manager/
```

## Project Layout

```
api/
  server.py            # FastAPI application (main API)
  model_inspector.py   # Format detection, VRAM estimation, GPU info
app/src/llm_manager/
  utils.py             # Shared: env loading, hash, config, dataset builder
  main.py              # Interactive CLI (menu-driven pipeline)
  train_lora.py        # Single-GPU LoRA trainer
  train_lora_dual.py   # Multi-GPU (DDP) LoRA trainer
  merge_lora.py        # Merge LoRA adapters into base model
  convert_lora.py      # Convert merged model to EXL2
  switch_model.py      # CLI model switcher
  download_models.py   # Download from Hugging Face
  download_convert_chat_model.py  # Download + convert to EXL2
  test_intent_models.py           # Benchmark LoRA models
  validate_training_data.py       # Validate training data against assistant
config/
  settings.example.env # Template for secrets/.env
data/
  *_prompts.jsonl      # Training data per intent category
model_configs.json     # Model definitions (training + runtime)
web/
  index.html           # Dashboard UI
  app.js               # Dashboard logic
secrets/
  .env                 # Actual configuration (git-ignored)
```

## Environment Variables (secrets/.env)

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_CHAT_API_BASE` | Chat engine OpenAI API base | `http://127.0.0.1:8500` |
| `LLM_INTENT_API_BASE` | Intent engine API base | `http://127.0.0.1:8501` |
| `LLM_SMALL_API_BASE` | Small/utility engine API base | `http://127.0.0.1:8502` |
| `SMART_ASSISTANT_URL` | Smart Assistant /command endpoint | `http://127.0.0.1:8100/command` |
| `CUDA_VISIBLE_DEVICES` | GPU(s) for training/conversion | `0` |
| `PM2_CHAT` / `PM2_INTENT` / `PM2_SMALL` | Legacy PM2 process names | `llm_a_8500` etc. |
| `WEBUI_ROOT` | text-generation-webui install dir | `/srv/2bananas/engines/text-generation-webui` |
| `WEBUI_MODELS_DIR` | Shared models directory | `/srv/2bananas/engines/models` |
| `EXLLAMA_ROOT` | ExLlamaV2 install dir | `/srv/2bananas/engines/exllamav2` |
| `ENABLE_CHAT` / `ENABLE_INTENT` / `ENABLE_SMALL` | Show slot in dashboard (0/1) | `1` |
| `HF_TOKEN` | Hugging Face auth token | (none) |

## API Reference

### Core

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service health + engine pings |
| GET | `/system` | CPU load, RAM, disk |
| GET | `/models` | List all models + active links + slot visibility + metadata |
| POST | `/switch` | Switch model: `{ mode, model_dir, bounce }` |
| POST | `/bounce/{mode}` | Restart engine (chat/intent/small) |
| GET/POST | `/knobs` | Read/write .env settings |

### Model Inspection

| Method | Path | Description |
|--------|------|-------------|
| GET | `/inspect` | Inspect all models (format, loader, VRAM estimate) |
| GET | `/inspect/{name}` | Inspect single model |
| GET | `/vram` | Live GPU VRAM from nvidia-smi |

### Engine Control

| Method | Path | Description |
|--------|------|-------------|
| GET | `/engines/status` | All engines: systemd state, port, active model |
| POST | `/engines/{mode}/{action}` | start/stop/restart a specific engine |
| POST | `/engines/solo/{mode}` | Stop others, start one |
| GET | `/engines/{mode}/logs` | Journal tail |

### Jobs (Train / Merge / Convert)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/jobs` | List all jobs |
| GET | `/jobs/{id}` | Job detail + log tail |
| POST | `/jobs` | Start job: `{ kind, model_key, force, ... }` |
| POST | `/jobs/{id}/cancel` | Cancel running job |

### Test Prompts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/test-chat?q=...` | Send test prompt to chat engine |
| GET | `/test-intent?q=...` | Test intent engine |
| GET | `/test-util?q=...` | Test small/utility engine |

## Supported Model Formats

The inspector auto-detects and recommends the correct loader:

| Format | Detection | Recommended Loader |
|--------|-----------|-------------------|
| **EXL2** | `.exl2` files or name contains `-exl2` | exllamav2 |
| **EXL3** | `.exl3` files | exllamav2 (≥0.3) |
| **GGUF** | `.gguf` files | llama.cpp |
| **AWQ** | `quantization_config.quant_method == "awq"` | exllamav2 |
| **GPTQ** | `quantization_config.quant_method == "gptq"` | exllamav2 |
| **FP16/BF16/FP8** | safetensors + config.json torch_dtype | transformers |
| **LoRA** | `adapter_config.json` present | transformers |

## Multi-GPU Notes

- **GPU 0:** Dedicated to chat (large model, e.g. 8B+ at 4-6 bpw)
- **GPU 1:** Shared between intent (LoRA models) and small/utility
- **Spanning models across GPUs:** Use `--gpu-split` in your systemd unit:
  ```
  ExecStart=... --gpu-split 12,12   # Split 12GB on each GPU
  ```
- **VRAM headroom for context:** Each 1K tokens of context window costs ~32 MB per 1B parameters. A 7B model at 16K context uses ~3.5 GB for KV cache alone.

## VRAM Budget Guide

| Model Size | BPW | Weight VRAM | + 8K ctx KV | + 16K ctx KV |
|-----------|-----|-------------|-------------|--------------|
| 3B | 6.5 | ~2.4 GB | ~3.2 GB | ~4.0 GB |
| 7B | 4.0 | ~3.5 GB | ~5.3 GB | ~7.0 GB |
| 7B | 6.5 | ~5.7 GB | ~7.5 GB | ~9.2 GB |
| 8B | 6.5 | ~6.5 GB | ~8.5 GB | ~10.5 GB |
| 13B | 4.0 | ~6.5 GB | ~9.8 GB | ~13.0 GB |
| 14B | 4.0 | ~7.0 GB | ~10.5 GB | ~14.0 GB |
| 14B | 6.5 | ~11.4 GB | ~14.9 GB | ~18.4 GB |
| 70B | 3.0 | ~26 GB | — | — (needs split) |

*Estimates vary by architecture. Check `/vram` endpoint for live usage.*
