# LLM Manager

Production control plane for multi-slot LLM serving on dual-GPU systems.
Manages **text-generation-webui** instances via systemd, with model switching, format auto-detection, LoRA training pipeline, and a web dashboard.

Additional local docs:
- `docs/API.md`
- `docs/CLI_SYSOP_GUIDE.md`
- `docs/guides/EXTERNAL_INTEGRATION.md`

Baseline quality reports:
- `python run/baseline_local_models.py`
- writes `docs/reports/baseline_local_models_<timestamp>.{json,md}`
- writes `docs/reports/promotion_recommendation_<timestamp>.{json,md}`

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

## Current Deployment Model

- The FastAPI control plane runs from `api/server.py`
- By default it binds to `127.0.0.1:8101`
- Apache is expected to reverse-proxy `/llm-manager-api` to that local API
- The dashboard is the static UI in `web/index.html` with domain modules under `web/js/domains/`
- Runtime serving stays in `text-generation-webui`; the API manages it rather than serving models itself
- The deployed systemd engine units launch through `run/engine_launcher.py`

Typical slot layout:
- `chat` on port `8500`
- `intent` on port `8501`
- `small` on port `8502`

Current host deployment note:
- The deployed `llm-manager-api.service` sets `SYSTEMD_LLM_A=llm-a.service`, `SYSTEMD_LLM_B=llm-b.service`, and `SYSTEMD_LLM_C=llm-c.service`
- The deployed unit overrides `SERVER_MODELS_DIR` and `MODELS_DIR` to `/srv/2bananas/engines/text-generation-webui/user_data/models`
- On this host, that effective runtime models path currently mirrors the model inventory and active symlinks used by the engines

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
  provider_models.json # Curated provider model catalog
  provider_policies.json # Routing/default policy config
data/
  *_prompts.jsonl      # Training data per intent category
run/
  logs/                # API-launched job logs
  engine_launcher.py   # Loader auto-detection helper for systemd launches
  launch_tgw.py        # TGW launcher wrapper (backend lane)
  launch_vllm.py       # vLLM launcher wrapper (backend lane)
  launch_tabbyapi.py   # TabbyAPI launcher wrapper (backend lane)
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
| `TGW_CHAT_WEBUI_ENABLED` | Enable TGW WebUI for chat (0/1) | `0` |
| `TGW_CHAT_WEBUI_PORT` | TGW WebUI listen port for chat | `7860` |
| `TGW_CHAT_WEBUI_BIND_HOST` | TGW WebUI bind host | `127.0.0.1` |
| `TGW_CHAT_WEBUI_PUBLIC_URL` | Optional TGW WebUI public URL override | (none) |
| `HF_TOKEN` | Hugging Face auth token | (none) |

Process/service environment commonly used in deployment:

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_MANAGER_HOST` | API bind host | `127.0.0.1` |
| `LLM_MANAGER_PORT` | API bind port | `8101` |
| `SYSTEMD_LLM_A` | Systemd unit name for chat slot | (none) |
| `SYSTEMD_LLM_B` | Systemd unit name for intent slot | (none) |
| `SYSTEMD_LLM_C` | Systemd unit name for small slot | (none) |
| `SERVER_MODELS_DIR` / `MODELS_DIR` | Effective runtime model directory override | (none) |

## API Reference

### Core

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service health + engine pings |
| GET | `/system` | CPU load, RAM, disk |
| GET | `/models` | List all models + active links + slot visibility + metadata |
| POST | `/switch` | Switch model: `{ mode, model_dir, bounce, backend? }` |
| POST | `/bounce/{mode}` | Restart engine (chat/intent/small) |
| GET/POST | `/knobs` | Read/write .env settings |
| GET | `/providers/models` | Read provider model catalog config |
| GET | `/providers/policies` | Read provider policy config |
| GET | `/providers/state` | Read provider runtime state scaffold |
| POST | `/providers/state/provider-model-flags` | Update provider-model manual-review and free-rotation flags |
| PUT | `/providers/models` | Validate/apply provider model governance document |
| POST | `/providers/models/rollback` | Roll back provider model document to last good snapshot |
| PUT | `/providers/policies` | Validate/apply provider policy governance document |
| POST | `/providers/policies/rollback` | Roll back provider policy document to last good snapshot |
| POST | `/providers/openrouter/refresh` | Refresh upstream OpenRouter metadata cache, optionally with rankings |
| POST | `/providers/openrouter/discover-free` | Manually build and optionally activate temporary OpenRouter free fallback candidates |
| GET | `/providers/openrouter/free-candidates` | Inspect the current manual OpenRouter free candidate pool |
| POST | `/router/chat` | Normalized broker chat entrypoint (initial dry-run slice) |
| POST | `/router/completions` | Normalized broker completions entrypoint |
| POST | `/router/embed` | Normalized broker embeddings entrypoint |
| GET | `/router/health` | Router config and decision-log health |
| GET | `/router/last-decisions` | Recent router decision logs |
| GET | `/router/usage-summary` | Aggregated token/request usage logs |
| GET | `/router/budget-state` | Budget guardrail and spend state snapshot |
| GET | `/router/queue-state` | Free-tier queue depth and pending items |
| GET | `/router/fallback-stats` | Fallback/error stats from routing decisions |
| POST | `/router/evaluate/local` | Run a local model evaluation suite with variants |
| POST | `/router/evaluate/local/async` | Queue a local evaluation run with priority |
| GET | `/router/evaluation-queue-state` | Inspect evaluation queue and worker status |
| POST | `/router/evaluation-queue/{run_id}/cancel` | Cancel a queued evaluation run |
| GET | `/router/evaluation-suites` | List stored evaluation suites |
| GET | `/router/evaluation-suites/{suite_name}/{suite_version}` | Fetch a stored suite definition |
| PUT | `/router/evaluation-suites/{suite_name}/{suite_version}` | Upsert a suite definition |
| DELETE | `/router/evaluation-suites/{suite_name}/{suite_version}` | Delete a stored suite definition |
| POST | `/router/evaluation-suites/{suite_name}/{suite_version}/rerun` | Rerun a stored suite (sync or async) |
| GET | `/router/evaluations` | List stored evaluation runs with optional filters |
| GET | `/router/evaluations/{run_id}` | Fetch a full stored evaluation run |
| GET | `/router/evaluations/{run_id}/report` | Fetch summary + recommendations for a run |
| GET | `/router/evaluations/{run_id}/compare-compact` | Fetch compact compare artifact for LLM adjudication |
| GET | `/router/evaluation-summary` | List recent evaluation reports |
| GET | `/router/evaluation-worker-config` | Show evaluation worker and cap settings |

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

Notes:
- All test endpoints also accept `no_thinking=1`
- Jobs are tracked in memory only and do not survive an API restart
- Job logs are written to `run/logs/`
- `/models.meta` includes `recommended_backend` and `fallback_backends`
- Slot backend preference is stored in `run/state/slot_backends.json` and returned by `/models`, `/health`, and `/engines/status`
- Provider config files live in `config/provider_models.json` and `config/provider_policies.json`
- Provider runtime state scaffold is persisted at `run/state/provider_runtime_state.json`
- Router request and response contract models live in `api/router/contracts.py`
- Provider adapters live in `api/providers/` for local, OpenRouter, and OpenAI
- `/router/chat` now performs real adapter-backed provider dispatch with policy-chain fallback
- `/router/completions` and `/router/embed` use the same policy-chain dispatch path
- OpenRouter free-tier requests are protectively throttled by a local rpm limiter and provider-model cooldown tracking
- `POST /providers/openrouter/refresh` updates a cached upstream OpenRouter catalog and free-model set for hardened free-tier cycling, and can enrich the cache with rankings using `include_rankings=true`
- `POST /providers/openrouter/discover-free` is a manual-only workflow that filters cached OpenRouter free models by size, popularity, context, family, and capabilities, then stores a temporary candidate pool in runtime state
- `GET /providers/openrouter/free-candidates` shows the temporary candidate pool and any manually activated `active_ids`
- The `openrouter.free` lane only uses manually activated temporary candidates after curated free models are exhausted
- Free-tier overflow behavior now follows policy `queue_behavior` (`wait`, `fail_fast`, `fallback_to_local`, `upgrade_to_paid`)
- Provider budget guardrails can be configured in `config/provider_policies.json` (`budget`) and inspected at `/router/budget-state`
- Runtime spend and budget state are persisted in `run/state/provider_runtime_state.json` (`spend_logs`, `budget_state`)
- Local evaluation runs can compare prompt/system/temperature variants and store recommendations for tuning
- Local evaluation runs now support case-level and suite-level pass thresholds for stricter tuning gates
- Local evaluation queue supports priority lanes (`interactive`, `batch`, `evaluation`) for async runs
- Dashboard now includes an Evaluation Ops panel showing queue health, latest reports, and suite rerun controls
- Dashboard now includes a Router Ops panel for fallback health, budget state, free-tier queue pressure, provider model flags, and manual OpenRouter free-candidate discovery/activation
- Local evaluation candidate_models now support mixed provider targets (for example `chat_active_model`, `openrouter:model_id`, `openai:model_id`)
- `/router/evaluations` supports filtering by status, target mode, project, candidate model, provider, lane, suite pass, tag, and since timestamp
- Evaluation summaries now include by-provider aggregates and estimated-cost totals when provider catalog pricing metadata is available

## Operating Notes

- Model switching is done by changing symlinks in the shared models directory:
  - `chat_active_model`
  - `intent_active_model`
  - `small_active_model`
- `POST /switch` is the canonical switch entrypoint
- Engine control is systemd-first via `SYSTEMD_LLM_A`, `SYSTEMD_LLM_B`, and `SYSTEMD_LLM_C`
- `POST /bounce/{mode}` can still fall back to legacy PM2 names when systemd unit env vars are absent
- The dashboard hides slots when `ENABLE_CHAT`, `ENABLE_INTENT`, or `ENABLE_SMALL` is set to `0`
- The currently deployed engine units launch `run/engine_launcher.py`, which auto-detects the loader from the selected model contents

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
