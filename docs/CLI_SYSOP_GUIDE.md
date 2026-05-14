# CLI Operations Guide — LLM Manager

Historical note: this file keeps its old filename for compatibility, but it is not a SysOp integration guide.

Quick-reference for command-line operations. All commands assume you're in the project root with venv activated.

```bash
cd /srv/2bananas/projects/llm-manager
source venv/bin/activate
```

---

## 1. Engine Control (systemd)

This project is operated systemd-first. The API can still fall back to legacy PM2 names for `POST /bounce/{mode}`, but the current dashboard and engine-control endpoints assume configured systemd units.

On the deployed host, `llm-a`, `llm-b`, and `llm-c` all launch through `run/engine_launcher.py` rather than calling `text-generation-webui/server.py` directly.

Check engines:
- `sudo systemctl status llm-a llm-b llm-c llm-manager-api`

Restart chat:
- `sudo systemctl restart llm-a`

Stop all:
- `sudo systemctl stop llm-a llm-b llm-c`

Solo chat:
- `sudo systemctl stop llm-b llm-c && sudo systemctl start llm-a`

Logs:
- `sudo journalctl -u llm-a -n 100 --no-pager`
- `sudo journalctl -u llm-manager-api -f`

API equivalents:
- `curl http://localhost:8101/engines/status | python3 -m json.tool`
- `curl -X POST http://localhost:8101/engines/chat/restart`
- `curl -X POST http://localhost:8101/engines/solo/chat`
- `curl "http://localhost:8101/engines/chat/logs?lines=100" | python3 -m json.tool`

Standalone TGW WebUI service (one-off model testing):
- `curl http://localhost:8101/engines/tgw-webui/status | python3 -m json.tool`
- `curl -X POST http://localhost:8101/engines/tgw-webui/start`
- `curl -X POST http://localhost:8101/engines/tgw-webui/stop`
- `curl -X POST http://localhost:8101/engines/tgw-webui/restart`
- `curl "http://localhost:8101/engines/tgw-webui/logs?lines=100" | python3 -m json.tool`

---

## 2. Model Switching

Preferred via API:

- POST `/switch`
  - Body: `{ "mode": "chat|intent|small", "model_dir": "...", "bounce": true, "backend": "tgw|vllm|tabbyapi" }`

Examples:

    curl -X POST http://localhost:8101/switch \
      -H 'Content-Type: application/json' \
      -d '{"mode":"chat","model_dir":"Qwen3-14B-exl2","bounce":true}'

    curl -X POST http://localhost:8101/switch \
      -H 'Content-Type: application/json' \
      -d '{"mode":"intent","model_dir":"lora_llama3.2-3b","bounce":true}'

CLI options:

- `cd app/src/llm_manager`
- `python switch_model.py --chat <dir>`
- `python switch_model.py --intent <dir>`
- `python switch_model.py`  # interactive

Operational note:
- `/switch` updates `chat_active_model`, `intent_active_model`, or `small_active_model` in the shared models directory and then optionally restarts the engine.
- When `backend` is provided, llm-manager stores per-slot backend preference in `run/state/slot_backends.json`.

---

## 3. Model Inspection

- `GET /inspect`
- `GET /inspect/<model>`
- `GET /vram`
- `nvidia-smi`

Examples:

    curl http://localhost:8101/inspect | python3 -m json.tool
    curl http://localhost:8101/inspect/Qwen3-14B-exl2 | python3 -m json.tool
    curl http://localhost:8101/vram | python3 -m json.tool

---

## 4. Model Download

Located under:
- `app/src/llm_manager/`

Common scripts:
- `download_models.py`
- `download_convert_chat_model.py`

Examples:

    cd app/src/llm_manager
    python download_models.py --model_key llama3.2-3b
    python download_models.py --custom_model TheBloke/Mistral-7B-v0.1-GPTQ
    python download_convert_chat_model.py --repo_id Qwen/Qwen3-14B --bits 6.5

---

## 5. LoRA Training Pipeline

Primary entry:
- `python main.py --pipeline`

Common usage:

    cd app/src/llm_manager
    python main.py --pipeline --model_key llama3.2-3b --dual
    python main.py --pipeline --all --dual

Individual steps:
- `train_lora.py`
- `train_lora_dual.py`
- `merge_lora.py`
- `convert_lora.py`

Examples:

    python train_lora.py --model_key llama3.2-3b
    accelerate launch --num_processes 2 train_lora_dual.py --model_key llama3.2-3b
    python merge_lora.py --model_key llama3.2-3b
    python convert_lora.py --model_key llama3.2-3b
    python validate_training_data.py

Dual GPU uses `accelerate`.

---

## 6. Training Jobs via API

- `POST /jobs`
- `GET /jobs`
- `GET /jobs/<id>`
- `POST /jobs/<id>/cancel`

Examples:

    curl -X POST http://localhost:8101/jobs \
      -H 'Content-Type: application/json' \
      -d '{"kind":"train","model_key":"llama3.2-3b"}'

    curl http://localhost:8101/jobs | python3 -m json.tool

Notes:
- Jobs run as subprocesses from the project root.
- Job state is not persisted across an API restart.
- Logs are stored in `run/logs/`.

---

## 7. Health & Diagnostics

- `GET /health`
- `GET /system`
- `GET /engines/status`
- `GET /models`
- `GET /knobs`
- `GET /providers/models`
- `GET /providers/models/curated-summary`
- `GET /providers/policies`
- `GET /providers/state`
- `GET /router/health`
- `GET /router/last-decisions`
- `GET /router/decision-traces`
- `GET /router/usage-summary`
- `GET /router/queue-state`
- `GET /router/fallback-stats`
- `POST /router/evaluate/local`
- `POST /router/evaluate/local/async`
- `GET /router/evaluation-queue-state`
- `POST /router/evaluation-queue/{run_id}/cancel`
- `GET /router/evaluation-suites`
- `GET /router/evaluation-suites/{suite_name}/{suite_version}`
- `PUT /router/evaluation-suites/{suite_name}/{suite_version}`
- `DELETE /router/evaluation-suites/{suite_name}/{suite_version}`
- `POST /router/evaluation-suites/{suite_name}/{suite_version}/rerun`
- `GET /router/evaluations`
- `GET /router/evaluations/{run_id}`
- `GET /router/evaluations/{run_id}/report`
- `GET /router/evaluations/{run_id}/compare-compact`
- `GET /router/evaluation-summary`
- `GET /router/evaluation-worker-config`
- `POST /router/completions`
- `POST /router/embed`

Useful commands:

    curl http://localhost:8101/health | python3 -m json.tool
    curl http://localhost:8101/system | python3 -m json.tool
    curl http://localhost:8101/models | python3 -m json.tool
    curl http://localhost:8101/knobs | python3 -m json.tool
    curl http://localhost:8101/providers/models | python3 -m json.tool
    curl "http://localhost:8101/providers/models/curated-summary?limit=50" | python3 -m json.tool
    curl http://localhost:8101/providers/policies | python3 -m json.tool
    curl http://localhost:8101/providers/state | python3 -m json.tool
    curl http://localhost:8101/router/health | python3 -m json.tool
    curl "http://localhost:8101/router/last-decisions?limit=20" | python3 -m json.tool
    curl "http://localhost:8101/router/decision-traces?limit=20&compact=true" | python3 -m json.tool
    curl "http://localhost:8101/router/usage-summary?limit=200" | python3 -m json.tool
    curl http://localhost:8101/router/queue-state | python3 -m json.tool
    curl "http://localhost:8101/router/fallback-stats?limit=500" | python3 -m json.tool
    curl "http://localhost:8101/router/evaluation-queue-state?limit=50" | python3 -m json.tool
    curl "http://localhost:8101/router/evaluation-suites?limit=50" | python3 -m json.tool
    curl "http://localhost:8101/router/evaluations?status=completed&target_mode=chat&provider=local&limit=20" | python3 -m json.tool
    curl "http://localhost:8101/router/evaluation-summary?limit=20" | python3 -m json.tool
    curl "http://localhost:8101/router/evaluation-worker-config" | python3 -m json.tool
    curl -X POST http://localhost:8101/router/completions -H 'Content-Type: application/json' -d '{"prompt":"Say OK","max_tokens":8}' | python3 -m json.tool
    curl -X POST http://localhost:8101/router/embed -H 'Content-Type: application/json' -d '{"input":"hello world"}' | python3 -m json.tool

Local evaluation quickstart:

    curl -X POST http://localhost:8101/router/evaluate/local \
      -H 'Content-Type: application/json' \
      -d '{
        "suite_name":"chat-tuning-weather",
        "suite_version":"1",
        "target_mode":"chat",
        "case_pass_threshold_pct":0.8,
        "suite_pass_threshold_pct":0.9,
        "candidate_models":["chat_active_model"],
        "variants":[
          {"variant_id":"temp_0_2","temperature":0.2,"max_tokens":120},
          {"variant_id":"temp_0_7","temperature":0.7,"max_tokens":120}
        ],
        "cases":[
          {
            "case_id":"wx1",
            "prompt":"Give a one-line weather summary",
            "expected_contains":["weather"],
            "min_plugin_score_pct":1.0,
            "scoring_plugins":[
              {"name":"contains_any","terms":["weather","forecast"],"weight":1}
            ]
          }
        ]
      }' | python3 -m json.tool

    # Then inspect detailed run and compact report using the returned run_id
    curl "http://localhost:8101/router/evaluations/<run_id>" | python3 -m json.tool
    curl "http://localhost:8101/router/evaluations/<run_id>/report" | python3 -m json.tool
    curl "http://localhost:8101/router/evaluations/<run_id>/compare-compact" | python3 -m json.tool

Mixed-provider candidate example:

    curl -X POST http://localhost:8101/router/evaluate/local \
      -H 'Content-Type: application/json' \
      -d '{
        "suite_name":"cross-provider-weather",
        "suite_version":"1",
        "target_mode":"chat",
        "candidate_models":[
          "chat_active_model",
          "openrouter:meta-llama/llama-3.3-8b-instruct:free",
          "openai:gpt-4o-mini"
        ],
        "variants":[{"variant_id":"baseline","temperature":0.2,"max_tokens":120}],
        "cases":[{"case_id":"wx1","prompt":"Give a one-line weather summary","expected_contains":["weather"]}]
      }' | python3 -m json.tool

Async evaluation queue:

    curl -X POST "http://localhost:8101/router/evaluate/local/async?priority=interactive" \
      -H 'Content-Type: application/json' \
      -d '{
        "suite_name":"chat-tuning-weather",
        "suite_version":"1",
        "target_mode":"chat",
        "candidate_models":["chat_active_model"],
        "variants":[{"variant_id":"temp_0_2","temperature":0.2,"max_tokens":120}],
        "cases":[{"case_id":"wx1","prompt":"Give a one-line weather summary","expected_contains":["weather"]}]
      }' | python3 -m json.tool

    curl "http://localhost:8101/router/evaluation-queue-state?limit=50" | python3 -m json.tool
    curl -X POST "http://localhost:8101/router/evaluation-queue/<run_id>/cancel" | python3 -m json.tool
    curl "http://localhost:8101/router/evaluations?status=completed&provider=openrouter&lane=openrouter_free&tag=weather&suite_pass=true&limit=20" | python3 -m json.tool

Suite reuse and reruns:

    curl -X PUT "http://localhost:8101/router/evaluation-suites/chat-tuning-weather/1" \
      -H 'Content-Type: application/json' \
      -d '{
        "suite_name":"chat-tuning-weather",
        "suite_version":"1",
        "target_mode":"chat",
        "candidate_models":["chat_active_model"],
        "variants":[{"variant_id":"temp_0_2","temperature":0.2,"max_tokens":120}],
        "cases":[{"case_id":"wx1","prompt":"Give a one-line weather summary","expected_contains":["weather"]}]
      }' | python3 -m json.tool

    curl "http://localhost:8101/router/evaluation-suites/chat-tuning-weather/1" | python3 -m json.tool
    curl -X POST "http://localhost:8101/router/evaluation-suites/chat-tuning-weather/1/rerun" \
      -H 'Content-Type: application/json' \
      -d '{"async_run":true,"priority":"batch","case_pass_threshold_pct":0.85,"suite_pass_threshold_pct":0.95}' | python3 -m json.tool

Operational note:
- Use `/engines/status` for complete slot state. `/health` is only a quick readiness snapshot and does not replace engine status inspection.

---

## 8. Dashboard

The web dashboard lives in `web/` and talks to the API through Apache at `/llm-manager-api`.

Operator expectations:
- Use it for day-to-day switching, restarts, log tails, and prompt tests
- Slot cards disappear when `ENABLE_CHAT`, `ENABLE_INTENT`, or `ENABLE_SMALL` is set to `0` in `secrets/.env`
- The dashboard supports test calls for `chat`, `intent`, and `small`

---

## 9. Strict Baseline Reports

Use the strict baseline harness to generate reproducible quality and promotion artifacts:

```bash
python run/baseline_local_models.py
```

Generated under `docs/reports/`:
- `baseline_local_models_<timestamp>.json`
- `baseline_local_models_<timestamp>.md`
- `promotion_recommendation_<timestamp>.json`
- `promotion_recommendation_<timestamp>.md`

---

## 10. Testing

```bash
# Test chat engine
curl "http://localhost:8101/test-chat?q=Hello" | python3 -m json.tool

# Test intent engine
curl "http://localhost:8101/test-intent?q=What%20time%20is%20it" | python3 -m json.tool

# Test small engine
curl "http://localhost:8101/test-util?q=Say%20OK" | python3 -m json.tool

# Disable thinking mode when supported by the backend
curl "http://localhost:8101/test-chat?q=Hello&no_thinking=1" | python3 -m json.tool

# Benchmark all intent models (interactive)
cd app/src/llm_manager
python test_intent_models.py

# Validate training data against Smart Assistant
python validate_training_data.py
```

---

## 11. File Locations

| What | Path |
|------|------|
| API server | `api/server.py` |
| Model inspector | `api/model_inspector.py` |
| CLI tools | `app/src/llm_manager/` |
| Shared utilities | `app/src/llm_manager/utils.py` |
| Configuration | `secrets/.env` |
| Config template | `config/settings.example.env` |
| Provider model catalog | `config/provider_models.json` |
| Provider policy config | `config/provider_policies.json` |
| Model configs | `model_configs.json` |
| Training data | `data/*_prompts.jsonl` |
| Job logs | `run/logs/` |
| Provider runtime state | `run/state/provider_runtime_state.json` |
| Router contracts | `api/router/contracts.py` |
| Engine launcher | `run/engine_launcher.py` |
| Dashboard | `web/` |
| Effective runtime models directory | `/srv/2bananas/engines/text-generation-webui/user_data/models/` |
| Shared/backing models directory | `/srv/2bananas/engines/models/` |
| Active symlinks | `/srv/2bananas/engines/text-generation-webui/user_data/models/{chat,intent,small}_active_model` |
| Systemd units | `/etc/systemd/system/llm-{a,b,c}.service` |
| API systemd unit | `/etc/systemd/system/llm-manager-api.service` |

Operational note:
- `run/state/provider_runtime_state.json` now tracks router request logs, usage logs, provider-model cooldowns, and OpenRouter free-tier limiter state.
