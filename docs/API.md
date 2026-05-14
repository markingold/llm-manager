# llm-manager API Reference

Public HTTP surface for the local control API in api/server.py.

## Base URL

- Local: http://127.0.0.1:8101/
- Apache typically exposes this behind /llm-manager-api
- Bind host and port can be overridden with LLM_MANAGER_HOST and LLM_MANAGER_PORT

Current deployed unit wiring on this host:
- SYSTEMD_LLM_A=llm-a.service
- SYSTEMD_LLM_B=llm-b.service
- SYSTEMD_LLM_C=llm-c.service
- SERVER_MODELS_DIR=/srv/2bananas/engines/text-generation-webui/user_data/models
- MODELS_DIR=/srv/2bananas/engines/text-generation-webui/user_data/models

## Configuration Sources

- For llm-manager-managed settings, the API reads `secrets/.env` first and then prefers non-empty values from `/srv/2bananas/secrets/global.env`.
- The shared global env path can be overridden with `LLM_MANAGER_GLOBAL_ENV_PATH`.
- This keeps project-local `secrets/.env` values as fallback when a shared key is absent.

## Core Endpoints

### Health
- GET /health
  - Lightweight status payload
  - Includes active symlink targets and configured API bases
  - Includes slot_backends
  - Performs quick connectivity checks for chat and small

### System
- GET /system
  - Load average, disk usage, and memory snapshot

### Models
- GET /models
  - Returns available model lists for chat, intent, and small
  - Includes active symlink targets
  - Includes slot_backends
  - Includes slots_enabled
  - Includes per-model metadata from the inspector
  - Includes `converted_artifacts` for managed EXL2 conversion outputs
  - Inspector metadata includes recommended_backend and fallback_backends

### Model Switching
- POST /switch
  - Body: { mode, model_dir, bounce, backend? }
  - mode supports chat, intent, small, and legacy alias util
  - backend can be tgw, vllm, or tabbyapi
  - Updates the active symlink, then optionally bounces the target engine
  - If backend is provided, updates per-slot backend preference state

### Knobs
- GET /knobs
- POST /knobs
  - Reads merged runtime values (local + global-preferred merge)
  - Writes project-local `secrets/.env` only
  - GET response redacts sensitive keys containing KEY, TOKEN, SECRET, or PASSWORD

### Bounce
- POST /bounce/{mode}
  - Legacy route used by the UI and scripts
  - Prefers systemd unit restart
  - Falls back to configured PM2 process names when systemd unit env vars are absent

## Model Inspection

- GET /inspect
- GET /inspect/{model_dir}
- GET /vram
  - Returns parsed nvidia-smi output when available

## Engine Control

- GET /engines/status
  - Returns unit info, slot backend, port, listening state, and active model path for each slot
  - Includes top-level `tgw_webui` status for the standalone TGW WebUI service
- POST /engines/{mode}/{action}
  - action is start, stop, or restart
- GET /engines/tgw-webui/status
  - Returns standalone TGW WebUI systemd + launch URL state
- POST /engines/tgw-webui/{action}
  - action is start, stop, or restart for the standalone TGW WebUI service
- GET /engines/tgw-webui/logs?lines=160
  - Returns a journal tail for the standalone TGW WebUI service unit
- POST /engines/tgw-webui/config
  - Updates TGW WebUI bind/port/public URL settings in .env
  - Body: { port?, bind_host?, public_url? }
- POST /engines/solo/{mode}
  - Stops the other configured units, then starts the requested one
- GET /engines/{mode}/logs?lines=160
  - Returns a journal tail for the configured systemd unit

Engine endpoints require SYSTEMD_LLM_A, SYSTEMD_LLM_B, and SYSTEMD_LLM_C to be configured for their respective slots.
Standalone TGW WebUI endpoints use SYSTEMD_TGW_WEBUI and default to llm-tgw-webui.service.

On the deployed host, the corresponding units invoke run/engine_launcher.py, which forwards to run/launch_tgw.py and forces API-only mode (`--no-webui`) for slot services.

## Provider Config and State

- GET /providers/models
  - Returns curated provider model catalog from config/provider_models.json
  - Includes deterministic document version hash for optimistic concurrency
  - Includes `local_conversion_artifacts` from runtime conversion metadata
- GET /providers/policies
  - Returns routing and policy config from config/provider_policies.json
  - Includes deterministic document version hash for optimistic concurrency
  - Supports optional task-scoped overrides under `task_overrides.chat|completion|embed`
  - Supports optional retention controls under `retention` for router audit logs, spend history, and lifecycle failure history
- GET /providers/state
  - Returns provider runtime state scaffold from run/state/provider_runtime_state.json
- GET /providers/retention-state
  - Returns effective retention settings plus current counts for request, usage, spend, governance, and provider lifecycle history rows
- POST /providers/state/provider-model-flags
  - Updates provider-model flags for manual review and free-rotation control
  - Body: { model_key, disabled_until_manual_review?, exclude_from_free_rotation?, reason, actor }
- PUT /providers/models
  - Safe governance write contract for provider model catalog
  - Body: { document, expected_version?, validate_only, reason, actor }
  - validate_only performs validation and version projection without writing
- POST /providers/models/rollback
  - Restores latest successful pre-change snapshot for provider model catalog
  - Body: { expected_version?, reason, actor }
  - Response includes audit_ref for post-mutation traceability
- PUT /providers/policies
  - Safe governance write contract for provider policies
  - Body: { document, expected_version?, validate_only, reason, actor }
- POST /providers/policies/rollback
  - Restores latest successful pre-change snapshot for provider policies
  - Body: { expected_version?, reason, actor }
  - Response includes audit_ref for post-mutation traceability
- POST /providers/policies/test
  - Evaluates effective routing policy resolution for a probe request
  - Supports task-aware and project-aware policy testing via `task_type`, `project_id`, and `provider_preferences`
- POST /providers/openrouter/refresh
  - Fetches upstream OpenRouter model metadata and updates runtime cache including detected free model ids
  - Query: include_rankings=true optionally scrapes OpenRouter rankings into the same cache for popularity-aware discovery
- POST /providers/openrouter/discover-free
  - Manually builds a temporary OpenRouter free-model candidate pool from cached upstream metadata
  - Body supports catalog refresh, rankings enrichment, size/context/popularity filters, family allow/deny, capability requirements, activate_top_n, and automatic smoke-check promotion controls (`auto_smoke_check`, `smoke_top_n`, `auto_promote_top_n`)
  - Writes results to runtime state only; does not modify config/provider_models.json
- GET /providers/openrouter/free-candidates
  - Returns the current manually discovered candidate pool, active_ids, and related catalog/rankings timestamps
- GET /providers/openrouter/rate-limit-state
  - Returns current OpenRouter free-tier limiter window counters and queue state by priority

## Router Endpoints

- POST /router/chat
  - Accepts normalized broker request shape for chat routing
  - Supports `project_id` for project-specific policy overrides
  - Applies `task_overrides.chat` and `project_overrides.<project>.task_overrides.chat` when configured
  - Optional request field `no_thinking` disables thinking for local provider dispatch (`enable_thinking=false`)
  - Request and response models are defined in api/router/contracts.py
  - Dispatches via provider adapters in api/providers for local, OpenRouter, and OpenAI
  - Uses policy candidate chains with fallback when allowed
  - Logs routing decisions and usage into provider runtime state
- POST /router/completions
  - Accepts normalized broker request shape for text completions
  - Supports `project_id` for project-specific policy overrides
  - Applies `task_overrides.completion` and `project_overrides.<project>.task_overrides.completion` when configured
  - Optional request field `no_thinking` disables thinking for local provider dispatch (`enable_thinking=false`)
  - Uses the same policy-chain dispatch and fallback model
- POST /router/embed
  - Accepts normalized broker request shape for embeddings
  - Supports `project_id` for project-specific policy overrides
  - Applies `task_overrides.embed` and `project_overrides.<project>.task_overrides.embed` when configured
  - Uses the same policy-chain dispatch and fallback model
- POST /router/route-test
  - Dry-run route resolution utility that returns strategy, candidate chain, selected models per lane, and cooldown/lifecycle hints
  - Optional `execute_first=true` runs a lightweight execution against the first eligible candidate for validation
- GET /router/health
  - Router config load status plus recent decision-log signal
  - Includes cached OpenRouter catalog, rankings freshness, and manual free-candidate counts
- GET /router/last-decisions?limit=20
  - Returns recent routing decision records
- GET /router/usage-summary?limit=200
  - Aggregates usage logs by provider across the selected window
- GET /router/budget-state
  - Returns budget guardrail thresholds, current day and month spend totals, and warning or exceeded flags
- GET /router/queue-state
  - Shows free-tier queue depth, pending and expired counts, and recent queued items
- GET /router/fallback-stats?limit=500
  - Aggregates fallback and error patterns from recent routing decisions
- POST /router/evaluate/local
  - Runs a local evaluation suite against selected candidate models and parameter variants
  - Supports comparing system prompts and generation settings such as temperature and top_p
  - Supports mixed provider candidates in candidate_models (for example local aliases, openrouter:model_id, openai:model_id)
  - Stores run outputs, summary metrics, and recommendations in provider runtime state
- POST /router/evaluate/local/async?priority=interactive|batch|evaluation
  - Enqueues a local evaluation suite for async execution
  - Priority controls worker scheduling order
- GET /router/evaluation-queue-state?limit=100
  - Returns queued/running/completed/error queue entries for evaluation jobs
- POST /router/evaluation-queue/{run_id}/cancel
  - Cancels a queued evaluation run
- GET /router/evaluation-suites
  - Lists stored evaluation suites
- GET /router/evaluation-suites/{suite_name}/{suite_version}
  - Fetches one stored suite definition
- PUT /router/evaluation-suites/{suite_name}/{suite_version}
  - Upserts one suite definition
- DELETE /router/evaluation-suites/{suite_name}/{suite_version}
  - Deletes one stored suite definition
- POST /router/evaluation-suites/{suite_name}/{suite_version}/rerun
  - Reruns a stored suite in sync or async mode
- GET /router/evaluations?status=&target_mode=&project=&model=&provider=&lane=&tag=&suite_pass=&since_ts=
  - Lists stored evaluation runs with optional filters for audit and triage
- GET /router/evaluations/{run_id}
  - Returns full stored run data, including per-case outputs and errors
- GET /router/evaluations/{run_id}/report
  - Returns the compact report payload for a run
- GET /router/evaluations/{run_id}/compare-compact
  - Returns grouped outputs optimized for side-by-side model/variant comparison and external LLM adjudication
- GET /router/evaluation-summary?limit=20
  - Returns recent reports to track tuning iterations over time
- GET /router/lane-sufficiency-report?limit_groups=20&limit_runs=500&target_mode=&project=&since_ts=&min_rows_per_lane=8&min_pass_rate=0.9&max_pass_gap=0.05
  - Compares lane pass rates and estimated costs by task group to identify when cheaper lanes are sufficient
  - Uses completed evaluation runs and returns reference-lane vs cheapest-sufficient-lane recommendations
- GET /router/evaluation-worker-config
  - Returns active worker count, per-priority running caps, and queue tuning values

## Protective Throttling and Cooldowns

- OpenRouter free-tier lanes are rate-limited locally using provider policy free_rate_limit_rpm (default 20 rpm)
- On free-tier overflow, behavior follows policy queue_behavior: wait, fail_fast, fallback_to_local, or upgrade_to_paid
- Free-tier queue scheduling is priority-aware (`interactive`, `batch`, `evaluation`) and can evict lower-priority queued entries when at capacity
- Rate-limited or retryable provider failures can place provider-model pairs into temporary cooldown windows
- Cooldown and limiter state are persisted under provider_model_state and provider_rate_limits in run/state/provider_runtime_state.json
- OpenRouter upstream model metadata can be refreshed and cached to harden free-tier routing decisions
- OpenRouter rankings enrichment is optional and is only fetched when operators call refresh or discovery with include_rankings enabled
- Automatic free-tier cycling now skips models marked in provider_model_state as disabled_until_manual_review or exclude_from_free_rotation
- Provider-model lifecycle now tracks promotion states (`discovered`, `candidate`, `smoke_passed`, `active`, `quarantined`, `retired`) and rolling failure-window metrics (`failure_count_24h`, `failure_count_7d`)
- Manual discovery results are stored under openrouter_free_candidates in run/state/provider_runtime_state.json
- The openrouter.free lane only consults manually activated active_ids after curated free entries are exhausted

## Pricing and Budget Guardrails

- Provider catalog entries in config/provider_models.json can include pricing metadata keys such as input_cost_usd_per_1k and output_cost_usd_per_1k
- Router decisions can record estimated request cost when pricing metadata is present
- Runtime spend tracking is stored in run/state/provider_runtime_state.json under budget_state and spend_logs
- Policy budget controls live under config/provider_policies.json budget:
  - daily_usd_limit
  - monthly_usd_limit
  - per_request_usd_limit
  - warn_threshold_pct
  - hard_fail_on_budget_exceeded
  - providers map to enable guardrails per provider

## Local Evaluation Pipeline

- Local evaluation artifacts are stored in run/state/provider_runtime_state.json under:
  - evaluation_suites
  - evaluation_runs
  - evaluation_reports
  - evaluation_queue
- This pipeline is intended for prompt and parameter tuning across local models for project-specific tasks
- Built-in summary and recommendations focus on:
  - success and error rates
  - expected-content hit rate (when expected_contains is provided)
  - latency, output length, and token usage by model and variant
- Suite reruns and compact compare artifacts support iterative prompt and temperature tuning cycles
- Cases can include scoring_plugins for pluggable checks such as regex_match, contains_any, contains_all, json_valid, and max_chars
- scoring_plugins entries use keys such as name, weight, and plugin-specific fields:
  - regex_match: pattern, flags
  - contains_any or contains_all: terms
  - json_valid: required_keys
  - max_chars: max_chars
- Case-level and suite-level threshold gates are supported to mark pass/fail outcomes during local tuning
- Filtered run listing supports focused review by status, mode, project, model, provider, lane, suite pass, tag, and timestamp window
- Per-result records and compare-compact outputs now include provider and lane fields for mixed-candidate adjudication
- Summary payloads include by_provider aggregates and estimated_cost_total_usd when pricing metadata exists in the provider catalog
- Lane sufficiency reports compare reference lanes to cheaper sufficient candidates using configurable pass-rate and row-count thresholds

## Jobs

- POST /jobs
- GET /jobs
- GET /jobs/{id}
- POST /jobs/{id}/cancel

Job payloads support:
- kind: train, merge, convert, or convert_hf_exl2
- model_key
- repo_id
- bits
- groupsize
- force
- train_all
- merge_all
- convert_all
- data_path
- base_models_dir
- webui_models_dir
- exllama_root

Notes:
- Jobs are launched as local subprocesses rooted at the project directory
- Generic job process state is kept in memory only
- Logs are written to run/logs/
- For `convert_hf_exl2`, run/artifact metadata is also persisted in run/state/provider_runtime_state.json

## Managed EXL2 Conversion

- POST /conversions/exl2
  - Starts managed EXL2 conversion from Hugging Face repo id
  - Body: { repo_id, bits, groupsize, force?, base_models_dir?, webui_models_dir?, exllama_root? }
- GET /conversions/exl2/jobs
  - Lists persisted EXL2 conversion runs
- GET /conversions/exl2/jobs/{job_id}
  - Returns one persisted conversion run with log tail
- GET /conversions/exl2/artifacts
  - Lists persisted EXL2 conversion artifacts and metadata
- GET /conversions/exl2/artifacts/{artifact_id}
  - Returns one converted artifact record

Persisted EXL2 metadata fields include source repo id/hash, bits, groupsize, output directory/model dir, timestamps, detected format/loader, and catalog sync result.

## Test Endpoints

- GET /test-chat?q=...
- GET /test-intent?q=...
- GET /test-util?q=...

All test endpoints also accept:
- no_thinking=1

They call the OpenAI-compatible /v1/chat/completions endpoint exposed by the underlying engine.

## Notes

- HTTP responses include `X-Request-Id`; request logs emit structured JSON including request_id, method, path, status, and duration_ms.

- Prefer switching models via API rather than manual symlink edits.
- Use /engines/status instead of /health when you need full operational state.
- /models is the best summary endpoint for available models, active symlinks, slot visibility, slot backends, and model metadata.
- The effective runtime model directory may differ from the default in api/server.py when overridden by the systemd service environment.
