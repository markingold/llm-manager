# CLI Sysop Guide — LLM Manager

Quick-reference for command-line operations. All commands assume you're in the project root with venv activated.

```bash
cd /srv/2bananas/projects/llm-manager
source venv/bin/activate
```

---

## 1. Engine Control (systemd)

```bash
# Check all engines
sudo systemctl status llm-a llm-b llm-c llm-manager-api

# Restart chat engine
sudo systemctl restart llm-a

# Stop all LLM engines
sudo systemctl stop llm-a llm-b llm-c

# Start just chat (solo)
sudo systemctl stop llm-b llm-c && sudo systemctl start llm-a

# View logs
sudo journalctl -u llm-a -n 100 --no-pager
sudo journalctl -u llm-manager-api -f   # follow API logs
```

## 2. Model Switching

```bash
# Via API (preferred — handles symlink + engine restart)
curl -X POST http://localhost:8101/switch \
  -H 'Content-Type: application/json' \
  -d '{"mode":"chat","model_dir":"Qwen3-14B-exl2","bounce":true}'

# Switch intent model
curl -X POST http://localhost:8101/switch \
  -d '{"mode":"intent","model_dir":"lora_llama3.2-3b","bounce":true}' \
  -H 'Content-Type: application/json'

# Via CLI (interactive)
cd app/src/llm_manager
python switch_model.py

# Via CLI (direct)
python switch_model.py --chat Qwen3-14B-exl2
python switch_model.py --intent lora_llama3.2-3b
```

## 3. Model Inspection

```bash
# Inspect all models
curl http://localhost:8101/inspect | python3 -m json.tool

# Inspect one model
curl http://localhost:8101/inspect/Qwen3-14B-exl2 | python3 -m json.tool

# Check VRAM
curl http://localhost:8101/vram | python3 -m json.tool

# Quick GPU check
nvidia-smi
```

## 4. Model Download

```bash
cd app/src/llm_manager

# Download a Hugging Face model directly into TGW models dir
python download_models.py --custom_model TheBloke/Mistral-7B-v0.1-GPTQ

# Download from model_configs.json
python download_models.py --model_key llama3.2-3b

# Download + convert to EXL2 (interactive)
python download_convert_chat_model.py

# Download + convert (scripted)
python download_convert_chat_model.py --repo_id Qwen/Qwen3-14B --bits 6.5
```

## 5. LoRA Training Pipeline

```bash
cd app/src/llm_manager

# Full pipeline: train → merge → convert → copy (interactive)
python main.py

# Full pipeline (scripted, single model, dual GPU)
python main.py --pipeline --model_key llama3.2-3b --dual

# Full pipeline (all models)
python main.py --pipeline --all --dual

# Individual steps:
python train_lora.py --model_key llama3.2-3b           # train single GPU
accelerate launch --num_processes 2 train_lora_dual.py --model_key llama3.2-3b  # dual GPU
python merge_lora.py --model_key llama3.2-3b            # merge LoRA into base
python convert_lora.py --model_key llama3.2-3b          # convert to EXL2

# Force retrain (ignore hash cache)
python train_lora.py --model_key llama3.2-3b --force

# Rebuild combined dataset from *_prompts.jsonl
python -c "from utils import build_combined_dataset; build_combined_dataset()"
```

## 6. Training Jobs via API

```bash
# Start training job
curl -X POST http://localhost:8101/jobs \
  -H 'Content-Type: application/json' \
  -d '{"kind":"train","model_key":"llama3.2-3b"}'

# Start merge job
curl -X POST http://localhost:8101/jobs \
  -d '{"kind":"merge","model_key":"llama3.2-3b"}' \
  -H 'Content-Type: application/json'

# List jobs
curl http://localhost:8101/jobs | python3 -m json.tool

# Job detail + log tail
curl http://localhost:8101/jobs/abc123def456 | python3 -m json.tool

# Cancel a job
curl -X POST http://localhost:8101/jobs/abc123def456/cancel
```

## 7. Health & Diagnostics

```bash
# API health check
curl http://localhost:8101/health | python3 -m json.tool

# System info (load, disk, RAM)
curl http://localhost:8101/system | python3 -m json.tool

# Engine status (all three)
curl http://localhost:8101/engines/status | python3 -m json.tool

# Engine logs
curl "http://localhost:8101/engines/chat/logs?lines=50" | python3 -m json.tool

# List models + active symlinks
curl http://localhost:8101/models | python3 -m json.tool

# Read/write knobs (.env settings)
curl http://localhost:8101/knobs | python3 -m json.tool
curl -X POST http://localhost:8101/knobs \
  -d '{"ENABLE_SMALL":"0"}' \
  -H 'Content-Type: application/json'
```

## 8. Testing

```bash
# Test chat engine
curl "http://localhost:8101/test-chat?q=Hello" | python3 -m json.tool

# Test intent engine
curl "http://localhost:8101/test-intent?q=What%20time%20is%20it" | python3 -m json.tool

# Benchmark all intent models (interactive)
cd app/src/llm_manager
python test_intent_models.py

# Validate training data against Smart Assistant
python validate_training_data.py
```

## 9. Common Scenarios

### Deploy a new chat model
```bash
# 1. Download
cd app/src/llm_manager
python download_convert_chat_model.py --repo_id Qwen/Qwen3-14B --bits 6.5

# 2. Verify it appeared
curl http://localhost:8101/models | python3 -m json.tool | grep Qwen

# 3. Inspect
curl http://localhost:8101/inspect/Qwen__Qwen3-14B_exl2_b6p5 | python3 -m json.tool

# 4. Switch + bounce
curl -X POST http://localhost:8101/switch \
  -d '{"mode":"chat","model_dir":"Qwen__Qwen3-14B_exl2_b6p5","bounce":true}' \
  -H 'Content-Type: application/json'

# 5. Test
curl "http://localhost:8101/test-chat?q=Hello"
```

### Retrain intent models after adding data
```bash
# 1. Add/edit data files in data/*_prompts.jsonl
# 2. Validate
cd app/src/llm_manager
python validate_training_data.py

# 3. Train all models
python main.py --pipeline --all --dual

# 4. Verify + switch
curl http://localhost:8101/models | python3 -m json.tool
python switch_model.py --intent lora_llama3.2-3b
```

### Hide the small/utility slot
```bash
# Via API
curl -X POST http://localhost:8101/knobs \
  -d '{"ENABLE_SMALL":"0"}' \
  -H 'Content-Type: application/json'

# Or edit secrets/.env directly
# ENABLE_SMALL=0

# Dashboard will hide the small engine card on next refresh
```

### Span a large model across both GPUs
Edit `/etc/systemd/system/llm-a.service` (or whichever unit):
```ini
ExecStart=/srv/2bananas/engines/text-generation-webui/venv/bin/python \
  /srv/2bananas/engines/text-generation-webui/server.py \
  --model chat_active_model \
  --loader exllamav2 \
  --gpu-split 20,20 \
  --max_seq_len 8192 \
  ...
```
Then:
```bash
sudo systemctl daemon-reload
sudo systemctl restart llm-a
```
**Note:** When spanning GPUs, the intent and small engines must be stopped (or moved to CPU).

## 10. File Locations

| What | Path |
|------|------|
| API server | `api/server.py` |
| Model inspector | `api/model_inspector.py` |
| CLI tools | `app/src/llm_manager/` |
| Shared utilities | `app/src/llm_manager/utils.py` |
| Configuration | `secrets/.env` |
| Config template | `config/settings.example.env` |
| Model configs | `model_configs.json` |
| Training data | `data/*_prompts.jsonl` |
| Dashboard | `web/index.html`, `web/app.js` |
| Models directory | `/srv/2bananas/engines/models/` |
| Active symlinks | `models/{chat,intent,small}_active_model` |
| Systemd units | `/etc/systemd/system/llm-{a,b,c}.service` |
| API systemd unit | `/etc/systemd/system/llm-manager-api.service` |
