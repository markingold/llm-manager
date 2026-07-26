# CLI Operations Guide — LLM Manager

Historical note: this file keeps its old filename for compatibility, but it is not a SysOp integration guide.

Quick-reference for command-line operations. All commands assume you're in the project root with venv activated.

Canonical engine and model layout runbook:
- `docs/guides/RB-ENGINES-LAYOUT.md`

```bash
cd /srv/2bananas/projects/llm-manager
source venv/bin/activate
```

---

## 1. Engine Control (systemd)

This project is operated systemd-first. The API can still fall back to legacy PM2 names for `POST /bounce/{mode}`, but the current dashboard and engine-control endpoints assume configured systemd units.

On the deployed host, `llm-a`, `llm-b`, and `llm-c` all launch through `run/engine_launcher.py` rather than calling `text-generation-webui/server.py` directly.

Startup guardrails (enabled by default in `run/launch_tgw.py`):
- stale TGW-like listeners on the target API port are terminated before launch
- stale ExLlama torch-extension lock files are removed before launch
- tuning knobs: `TGW_STARTUP_GUARDRAILS`, `TGW_GUARDRAIL_CLEAN_PORT`, `TGW_GUARDRAIL_TERM_TIMEOUT_SECONDS`, `TGW_GUARDRAIL_CLEAN_EXLLAMA_LOCKS`, `TGW_EXLLAMA_LOCK_STALE_SECONDS`, `TGW_EXLLAMA_LOCK_FORCE_REMOVE`, `TGW_EXLLAMA_LOCK_PATHS`

Check engines:
- `sudo systemctl status llm-a llm-b llm-c llm-embed llm-manager-api`

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
- `curl http://localhost:8101/backends | python3 -m json.tool`
- `curl -X POST http://localhost:8101/engines/chat/restart`
- `curl -X POST http://localhost:8101/engines/solo/chat`
- `curl "http://localhost:8101/engines/chat/logs?lines=100" | python3 -m json.tool`

Dashboard behavior note:
- Engine cards now show slot backend, active model format, and inspector recommendation/fallback context.
- Slot test actions are now backend-aware and can be disabled when the current backend/model pair is incompatible or a slot endpoint is unavailable.

Standalone TGW WebUI service (one-off model testing):
- `curl http://localhost:8101/engines/tgw-webui/status | python3 -m json.tool`
- `curl -X POST http://localhost:8101/engines/tgw-webui/start`
- `curl -X POST http://localhost:8101/engines/tgw-webui/stop`
- `curl -X POST http://localhost:8101/engines/tgw-webui/restart`
- `curl "http://localhost:8101/engines/tgw-webui/logs?lines=100" | python3 -m json.tool`

---

## 2. Model Switching

Preferred via API:

- POST `/models/load`
  - Body: `{ "mode": "chat|intent|small|embed", "model_dir": "...", "bounce": true, "backend": "tgw|vllm|tabbyapi|llamacpp" }`

Examples:

    curl -X POST http://localhost:8101/models/load \
      -H 'Content-Type: application/json' \
      -d '{"mode":"chat","model_dir":"Qwen3-14B-exl2","bounce":true}'

    curl -X POST http://localhost:8101/models/load \
      -H 'Content-Type: application/json' \
      -d '{"mode":"intent","model_dir":"lora_llama3.2-3b","bounce":true}'

The old `switch_model.py` and `main.py` direct serve/switch paths are retired.
They exit without mutating state because they bypassed readiness verification
and rollback.

Operational note:
- `/models/load` updates `chat_active_model`, `intent_active_model`, `small_active_model`, or `embed_active_model`, restarts the configured unit, verifies the exact served identity, and rolls back on failure.
- When `backend` is provided, llm-manager stores per-slot backend preference in `run/state/slot_backends.json`.
- When `backend` is omitted, llm-manager can auto-apply registry-backed inspector recommendations; inspect `backend_source` and `auto_backend_applied` in the response.

Embedding validation:

    curl http://127.0.0.1:8503/v1/embeddings \
      -H 'Content-Type: application/json' \
      -d '{"model":"BAAI__bge-small-en-v1.5","input":"hello world"}'

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
- Job state and process identity are persisted in `run/state/runtime.db`; API
  startup reconciles live, completed, and interrupted jobs.
- Logs are stored in `run/logs/`.

---

## 7. Health & Diagnostics

- `GET /health`
- `GET /ready`
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
    # /models now includes slot_endpoints with resolved local backend/base/port per slot
    curl http://localhost:8101/knobs | python3 -m json.tool
    # /knobs can include optional LLM_*_API_BASE_TGW|VLLM|TABBYAPI overrides
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
- Use `/health` for API liveness, `/ready` for routed capability readiness, and
  `/engines/status` for complete slot/unit state.

### Engine configuration preflight and restart-storm prevention

Safe config-only probe (does not launch a process):

    python run/engine_launcher.py \
      --api-port 8500 \
      --model chat_active_model \
      --max-seq-len 16384 \
      --preflight-only \
      --skip-port-check

Omit `--skip-port-check` only while the lane is stopped. A running healthy lane
already owns its port and should then report a temporary `port_collision`.

Failure classes:

- exit 78: static configuration (`missing_backend`, `missing_model`, incompatible
  backend/task/model, invalid numeric config); systemd does not restart it
- exit 75: temporary preflight failure such as a port collision; systemd may
  retry within the configured start limit
- other non-zero exit: backend/runtime crash; bounded `Restart=on-failure`
  recovery remains enabled

Inspect the one-line JSON event `engine_preflight_failed`. Its `incident_key` is
stable for alert deduplication and its detail is redacted. Correct configuration,
then use `systemctl reset-failed <unit>` and start the lane once.

---

## 8. Incident Runbook: Curated Admin + Decision Traces

Use this when routed requests are failing, flapping between providers, or showing unexpected fallback behavior.

1) Capture deterministic route traces first:

    curl "http://localhost:8101/router/decision-traces?limit=40&compact=true" | python3 -m json.tool

Focus on:
- `summary.by_reason_code`
- `summary.with_selected_fallback`
- per-trace `attempt_reason_codes`
- per-trace `fallback_summary`

2) Confirm policy resolution for the affected task/project:

    curl -X POST http://localhost:8101/providers/policies/test \
      -H 'Content-Type: application/json' \
      -d '{
        "task_type":"chat",
        "project_id":"<project_id>",
        "provider_preferences":{"strategy":"local_first"}
      }' | python3 -m json.tool

Verify `strategy_resolution`, `chain_resolution`, and `policy_context` to ensure strategy source, service-tier effects, fallback normalization, and dynamic-ranking diagnostics (`dynamic_ranking_enabled`, `dynamic_ranking_reason`, `dynamic_ranking_rows`) are what you expect.

3) Apply a targeted curated quick-admin change (no full JSON document edit):

Disable a failing OpenRouter free model temporarily:

    curl -X POST http://localhost:8101/providers/models/curated-entry \
      -H 'Content-Type: application/json' \
      -d '{
        "provider":"openrouter",
        "bucket":"free",
        "model_id":"meta-llama/llama-3.3-8b-instruct:free",
        "enabled":false,
        "actor":"ops-oncall",
        "reason":"incident: repeated fallback reason_code=dispatch_rate_limited"
      }' | python3 -m json.tool

Increase priority for a known-good paid fallback during incident mitigation:

    curl -X POST http://localhost:8101/providers/models/curated-entry \
      -H 'Content-Type: application/json' \
      -d '{
        "provider":"openrouter",
        "bucket":"paid",
        "model_id":"openai/gpt-4o-mini",
        "priority":1,
        "actor":"ops-oncall",
        "reason":"incident mitigation: elevate stable fallback"
      }' | python3 -m json.tool

4) Re-verify route behavior before closing incident:

    curl -X POST http://localhost:8101/router/route-test \
      -H 'Content-Type: application/json' \
      -d '{
        "task_type":"chat",
        "project_id":"<project_id>",
        "prompt":"incident verification probe",
        "provider_preferences":{"strategy":"local_first"}
      }' | python3 -m json.tool

Then refresh traces:

    curl "http://localhost:8101/router/decision-traces?limit=40&compact=true" | python3 -m json.tool

Expected stabilization signals:
- lower `summary.by_reason_code.dispatch_rate_limited` and related `dispatch_*` buckets
- lower `summary.by_reason_code.free_tier_limiter` during non-peak periods
- lower `summary.with_selected_fallback` after mitigation (unless strategy intentionally prefers fallback lanes)

---

## 9. Recovery Runbook: Manual Review + GPU/TGW Wedges

Use this when provider models are stuck in `manual_review` or `quarantined`, or when TGW engine processes are uninterruptible and local lanes cannot recover with normal restarts.

A) Clear manual-review or quarantine flags for provider models:

1) Inspect flagged rows and determine affected model IDs:

    curl "http://localhost:8101/providers/models/curated-summary?provider=openrouter&limit=400" | python3 -m json.tool
    curl "http://localhost:8101/providers/state" | python3 -m json.tool

`model_key` format for runtime flag updates is `provider:model_id`, for example:
- `openrouter:meta-llama/llama-3.3-8b-instruct:free`

2) Clear manual flags on the runtime row:

    curl -X POST http://localhost:8101/providers/state/provider-model-flags \
      -H 'Content-Type: application/json' \
      -d '{
        "model_key":"openrouter:meta-llama/llama-3.3-8b-instruct:free",
        "disabled_until_manual_review":false,
        "exclude_from_free_rotation":false,
        "actor":"ops-oncall",
        "reason":"recovery: cleared after provider/auth/path fix"
      }' | python3 -m json.tool

3) If the curated catalog row was intentionally disabled during mitigation, re-enable it:

    curl -X POST http://localhost:8101/providers/models/curated-entry \
      -H 'Content-Type: application/json' \
      -d '{
        "provider":"openrouter",
        "bucket":"free",
        "model_id":"meta-llama/llama-3.3-8b-instruct:free",
        "enabled":true,
        "actor":"ops-oncall",
        "reason":"recovery: re-enable after successful verification"
      }' | python3 -m json.tool

4) Re-verify policy and routing behavior:

    curl -X POST http://localhost:8101/providers/policies/test \
      -H 'Content-Type: application/json' \
      -d '{"task_type":"chat","provider_preferences":{"strategy":"free_first"}}' | python3 -m json.tool

    curl -X POST http://localhost:8101/router/route-test \
      -H 'Content-Type: application/json' \
      -d '{"task_type":"chat","prompt":"recovery verification probe"}' | python3 -m json.tool

    curl "http://localhost:8101/router/decision-traces?limit=40&compact=true" | python3 -m json.tool

Look for improving signals: fewer failure reason codes, lower fallback ratio, no repeated auth/manual-review quarantines for the recovered model, and expected dynamic-ranking outcomes in `chain_resolution`.

B) Recover from GPU-driver wedges or uninterruptible TGW processes:

1) Confirm wedge indicators:

    sudo systemctl status llm-a llm-b llm-c llm-embed llm-manager-api
    nvidia-smi
    ps -eo pid,ppid,stat,comm,args | rg 'llm-a|llm-b|llm-c|text-generation-webui|server.py'
    ps -eo pid,stat,comm,args | awk '$2 ~ /^D/ {print}'

2) Attempt non-reboot recovery first:

    sudo systemctl stop llm-a llm-b llm-c
    sudo systemctl restart llm-manager-api

If residual TGW processes are not in `D` state, terminate them explicitly and re-check process table.

3) Bring lanes back one at a time (sequential validation):

    sudo systemctl start llm-a
    curl http://localhost:8101/engines/status | python3 -m json.tool
    curl "http://localhost:8101/test-chat?q=recovery-check" | python3 -m json.tool

    sudo systemctl start llm-b
    curl "http://localhost:8101/test-intent?q=recovery-check" | python3 -m json.tool

    sudo systemctl start llm-c
    curl "http://localhost:8101/test-util?q=recovery-check" | python3 -m json.tool

4) Reboot criteria (perform controlled reboot if any are true):
- one or more TGW processes remain in `D` state after stop attempts
- `nvidia-smi` is hung or repeatedly fails with driver communication errors
- systemd start or stop actions repeatedly time out due to uninterruptible TGW processes

Controlled reboot sequence:

    sudo systemctl stop llm-a llm-b llm-c llm-embed llm-manager-api
    sudo sync
    sudo reboot

Post-reboot validation must remain single-lane until each lane passes its probe. Do not restart all three lanes in parallel during recovery.

---

## 10. Dashboard

The web dashboard lives in `web/` and talks to the API through Apache at `/llm-manager-api`.

Operator expectations:
- Use it for day-to-day switching, restarts, log tails, and prompt tests
- Slot cards disappear when `ENABLE_CHAT`, `ENABLE_INTENT`, or `ENABLE_SMALL` is set to `0` in `secrets/.env`
- The dashboard supports test calls for `chat`, `intent`, and `small`

Compact incident checklist (dashboard):
- Router view: click `Refresh Router` and `Refresh Decision Traces`.
- Read Router Ops pills in order: `reasons`, `selected fallback`, `queue`, `budget`.
- Providers/Budget view: set `actor` + `reason` before any mutation.
- If a curated model is failing: use Curated Model Quick Admin row `Apply` to disable or reprioritize in one click.
- Re-verify with Router view refresh; stabilization target is lower `selected fallback` and fewer non-`selected` reason buckets.

One-click rollback and restore guidance:
- One-click rollback: Providers/Budget -> Provider Governance -> choose `models` or `policies` -> click `Rollback Last Good`.
- One-click restore (row-level): Curated Model Quick Admin -> set row back to known-good values -> click row `Apply`.
- One-click restore (document-level): keep a known-good JSON snapshot in the editor and click `Apply` once to restore forward.
- After rollback or restore, always click `Refresh Governance State` then `Refresh Router` + `Refresh Decision Traces`.

---

## 11. Strict Baseline Reports

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

## 12. Testing

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

## 13. File Locations

| What | Path |
|------|------|
| API server | `llm_manager/server.py` |
| Model inspector | `llm_manager/model_inspector.py` |
| CLI tools | `app/src/llm_manager/` |
| Shared utilities | `app/src/llm_manager/utils.py` |
| Configuration | `secrets/.env` |
| Config template | `config/settings.example.env` |
| Provider model catalog | `config/provider_models.json` |
| Provider policy config | `config/provider_policies.json` |
| Model configs | `model_configs.json` |
| Training data | `data/*_prompts.jsonl` |
| Job logs | `run/logs/` |
| Provider runtime state | `run/state/runtime.db` (legacy JSON imported once) |
| Router contracts | `llm_manager/router/contracts.py` |
| Engine launcher | `run/engine_launcher.py` |
| Current host unit sources | `deploy/systemd/host/` |
| Dashboard | `web/` |
| Effective runtime models directory | `/srv/2bananas/engines/text-generation-webui/user_data/models/` |
| Shared/backing models directory | `/srv/2bananas/engines/models/` |
| Active symlinks | `/srv/2bananas/engines/text-generation-webui/user_data/models/{chat,intent,small}_active_model` |
| Systemd units | `/etc/systemd/system/llm-{a,b,c,embed}.service` or packaged `llm-manager-engine@.service` |
| API systemd unit | `/etc/systemd/system/llm-manager-api.service` |

Operational note:
- `run/state/runtime.db` stores router request/usage sections, provider-model cooldowns, and OpenRouter free-tier limiter state.
