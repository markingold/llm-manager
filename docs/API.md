# llm-manager API Reference

Public HTTP surface for the local control API in `llm_manager/server.py`.

## Base URL

- Local: http://127.0.0.1:8101/
- Apache typically exposes this behind /llm-manager-api
- Bind host and port can be overridden with LLM_MANAGER_HOST and LLM_MANAGER_PORT

Current deployed unit wiring on this host:
- SYSTEMD_LLM_A=llm-a.service
- SYSTEMD_LLM_B=llm-b.service
- SYSTEMD_LLM_C=llm-c.service
- SYSTEMD_LLM_D=llm-embed.service
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
  - Includes active symlink targets and backend-aware resolved API bases for chat/intent/small slots
  - Includes slot_backends
  - Performs quick connectivity checks for chat, intent, and small

### System
- GET /system
  - Load average, disk usage, and memory snapshot

### Models
- GET /models
  - Returns available model lists for chat, intent, and small
  - Intent remains unloaded when no active model is selected, while its choices include every installed chat-capable model supported by a local backend
  - Candidate lists are derived from inspector capabilities rather than model-directory naming conventions; unsupported and embedding-only checkpoints are excluded from text slots
  - Includes active symlink targets
  - Includes slot_backends
  - Includes `slot_endpoints` with resolved local slot mode/backend/base/port/base source metadata
  - Includes slots_enabled
  - Includes per-model metadata from the inspector
  - Includes `converted_artifacts` for managed EXL2/EXL3 conversion outputs
  - Inspector metadata includes recommended_backend and fallback_backends
  - Dashboard Operations uses `meta` + `slot_backends` + `slot_endpoints` to gate slot test actions by backend/model compatibility

### Model Switching
- POST /switch
  - Body: { mode, model_dir, bounce, backend?, lifecycle_mode?, native_max_seq_len? }
  - mode supports chat, intent, small, and legacy alias util
  - backend can be tgw, vllm, or tabbyapi
  - Serializes lifecycle mutations across API workers and atomically replaces the active symlink
  - Loads natively before committing state where supported; restart failures restore the prior symlink/backend
  - Rejects model paths outside the configured models root and rejects multimodal checkpoints until a compatible local lane exists
  - If backend is provided, updates per-slot backend preference state
  - If backend is omitted, switch auto-applies the model inspector's recommended backend (`tgw`, `vllm`, or `tabbyapi`)
  - If the selected backend is incompatible with the model kind, `/switch` returns HTTP 400 when backend was explicit, or auto-falls back to a compatible backend when backend was omitted
  - If no local backend supports the detected model kind, `/switch` returns HTTP 422 without mutating symlinks/backend state
  - If `vllm` is selected but unavailable in the current runtime, `/switch` returns HTTP 400 when explicit, or auto-falls back when omitted
  - lifecycle_mode controls backend-native behavior: `legacy` (default), `auto`, or `native`
  - `auto` attempts TabbyAPI native load for tabbyapi-backed slots and falls back to symlink+bounce when native load is unavailable
  - `native` requires the slot backend to be `tabbyapi` and returns HTTP 502 when native load fails
  - native_max_seq_len optionally forwards a context length hint during native TabbyAPI loads
  - If native_max_seq_len is omitted, native Tabby loads resolve it via env (`LLM_<MODE>_NATIVE_MAX_SEQ_LEN`, then `LLM_<MODE>_MAX_SEQ_LEN`, then `TABBYAPI_NATIVE_MAX_SEQ_LEN`) and finally default to `16384`
  - Response includes backend recommendation context (`backend_source`, `auto_backend_applied`, `model_kind`, `compatible_backends`, `unsupported_reason`, fallback list) plus lifecycle diagnostics (`native_load_attempted`, `native_load_used`, `native_load`, `bounce_result`)

### Model Lifecycle
- POST /models/load
  - Body: { mode, model_dir, bounce?, backend?, lifecycle_mode?, native_max_seq_len?, readiness_timeout_seconds? }
  - Explicit lifecycle-oriented alias for `/switch` that defaults to `bounce=true`
  - Requires a successful native load or engine restart; staged/no-op loads return an error
  - Polls the selected slot `/v1/models` endpoint and requires an exact requested model identity; timeout restores the prior symlink/backend/model
  - Reuses the same TabbyAPI native load behavior and fallback diagnostics as `/switch`
- POST /models/unload
  - Body: { mode, bounce?, backend?, lifecycle_mode? }
  - Attempts TabbyAPI native unload (`/v1/model/unload` then `/model/unload`) when backend and lifecycle mode permit
  - `native` mode requires tabbyapi backend and returns HTTP 502 on native unload failure
  - `auto` mode attempts native unload for tabbyapi-backed slots; other backends require `bounce=true` to stop the engine
  - Already-unloaded, backend-mismatch, and unavailable stop/unload cases return errors rather than successful no-ops
  - Response includes lifecycle diagnostics (`native_unload_attempted`, `native_unload_used`, `native_unload`, `bounce_result`)

### Knobs
- GET /knobs
- POST /knobs
  - Reads merged runtime values (local + global-preferred merge)
  - Writes project-local `secrets/.env` only
  - Supports optional per-backend slot base overrides: `LLM_*_API_BASE_TGW|VLLM|TABBYAPI`
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
  - Returns unit info, slot backend, resolved base, base source, port, listening state, and active model path for each slot
  - Includes top-level `tgw_webui` status for the standalone TGW WebUI service
  - Dashboard Operations uses this payload to surface slot backend context and disable unsupported slot tests when capability checks fail
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

Engine endpoints require `SYSTEMD_LLM_A` through `SYSTEMD_LLM_D` for chat, intent, small, and embedding slots respectively.
Standalone TGW WebUI endpoints use SYSTEMD_TGW_WEBUI and default to llm-tgw-webui.service.

On the deployed host, the corresponding units invoke run/engine_launcher.py, which dispatches to backend-specific launchers (`run/launch_tgw.py`, `run/launch_vllm.py`, `run/launch_tabbyapi.py`) based on slot backend preference.
TGW launches remain API-only by default (`--no-webui`) for slot services.
`run/launch_tgw.py` applies startup guardrails by default to reduce restart wedges:
- stale TGW-like listeners on the target API port are terminated before launch
- stale ExLlama torch-extension lock files are cleaned before launch
- behavior can be tuned via `TGW_STARTUP_GUARDRAILS`, `TGW_GUARDRAIL_CLEAN_PORT`, `TGW_GUARDRAIL_TERM_TIMEOUT_SECONDS`, `TGW_GUARDRAIL_CLEAN_EXLLAMA_LOCKS`, `TGW_EXLLAMA_LOCK_STALE_SECONDS`, `TGW_EXLLAMA_LOCK_FORCE_REMOVE`, and `TGW_EXLLAMA_LOCK_PATHS`

## Provider Config and State

`provider_models.json` and `provider_policies.json` use `schema_version: 2`. Older unversioned/v1 documents are migrated atomically with a one-time `.v1.bak`; documents newer than the running application fail closed. Runtime SQLite migrations are recorded in `schema_migrations`, and `/health` reports both supported schema versions.

- GET /providers/models
  - Returns curated provider model catalog from config/provider_models.json
  - Includes deterministic document version hash for optimistic concurrency
  - Includes `local_conversion_artifacts` from runtime conversion metadata
- GET /providers/policies
  - Returns routing and policy config from config/provider_policies.json
  - Includes deterministic document version hash for optimistic concurrency
  - Supports optional task-scoped overrides under `task_overrides.chat|completion|embed`
  - Supports optional `dynamic_ranking` controls for strategy-local lane scoring (`enabled`, `strategies`, weight tuning, and token estimate defaults)
- GET /providers/state
  - Returns provider runtime state reconstructed from versioned SQLite sections in `run/state/runtime.db`
- POST /providers/state/provider-model-flags
  - Updates provider-model flags for manual review and free-rotation control
  - Body: { model_key, disabled_until_manual_review?, exclude_from_free_rotation?, reason, actor }
  - model_key format is `provider:model_id` (example: `openrouter:meta-llama/llama-3.3-8b-instruct:free`)
  - Common incident-recovery flow: clear runtime flags here, then optionally re-enable curated rows with `POST /providers/models/curated-entry`
- GET /providers/models/curated-summary
  - Returns flattened curated model rows across `local.slots`, `local.converted_models`, `openrouter.free`, `openrouter.paid`, and `openai.allowed`
  - Supports optional filtering by `provider`, `bucket`, `enabled_only`, and `search`
- POST /providers/models/curated-entry
  - Applies targeted curated-row updates without full-document editing
  - Body: { provider, bucket, model_id, enabled?, priority?, backend?, notes?, expected_version?, reason, actor }
  - Uses the same governance/audit flow as full `PUT /providers/models`
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
  - Includes `strategy_resolution`, `chain_resolution`, and `policy_context` so operators can verify resolved strategy, chain source, strict-provider task constraints, service-tier influence, fallback normalization, and dynamic-ranking scoring details
- POST /providers/openrouter/refresh
  - Fetches upstream OpenRouter model metadata and updates runtime cache including detected free model ids
  - Query: include_rankings=true optionally scrapes OpenRouter rankings into the same cache for popularity-aware discovery
- POST /providers/openrouter/discover-free
  - Manually builds a temporary OpenRouter free-model candidate pool from cached upstream metadata
  - Body supports catalog refresh, rankings enrichment, size/context/popularity filters, family allow/deny, capability requirements, activate_top_n, and automatic smoke-check promotion controls (`auto_smoke_check`, `smoke_top_n`, `auto_promote_top_n`)
  - Candidate rows persist richer ranking fields when available (`top_weekly_rank`, `category_ranks`) and include smoke-check evidence in results
  - Writes results to runtime state only; does not modify config/provider_models.json
- GET /providers/openrouter/free-candidates
  - Returns the current manually discovered candidate pool, active_ids, and related catalog/rankings timestamps
  - Candidate rows include lifecycle and smoke evidence surfacing (`recent_promotion_transitions`, `recent_smoke_checks`, `lifecycle_evidence`)
- GET /providers/openrouter/rate-limit-state
  - Returns current OpenRouter free-tier limiter window counters and queue state by priority

## Router Endpoints

- POST /router/chat
  - Accepts normalized broker request shape for chat routing
  - Supports `project_id` for project-specific policy overrides
  - Applies `task_overrides.chat` and `project_overrides.<project>.task_overrides.chat` when configured
  - Optional request field `no_thinking` disables thinking for local provider dispatch (`enable_thinking=false`)
  - Request and response models are defined in `llm_manager/router/contracts.py`
  - Dispatches via provider adapters in `llm_manager/providers` for local, OpenRouter, and OpenAI
  - Local dispatch now resolves slot mode from selected local model alias and uses backend-aware slot base resolution (not chat-base only)
  - Uses policy candidate chains with fallback when allowed, including service-tier-aware chain selection and optional dynamic lane ranking when configured
  - Logs routing decisions and usage into provider runtime state
  - Decision records include deterministic `attempt_trace` and `fallback_summary` fields for incident triage
  - Dispatch failures now use typed reason codes (`dispatch_rate_limited`, `dispatch_auth_error`, etc.) and can mark subsequent same-provider attempts as blocked for auth failures
- POST /router/completions
  - Accepts normalized broker request shape for text completions
  - Supports `project_id` for project-specific policy overrides
  - Applies `task_overrides.completion` and `project_overrides.<project>.task_overrides.completion` when configured
  - Optional request field `no_thinking` disables thinking for local provider dispatch (`enable_thinking=false`)
  - Uses the same policy-chain dispatch and fallback model
  - Local dispatch resolves selected slot mode/backend from local model alias before selecting the target base
  - Decision records include deterministic `attempt_trace` and `fallback_summary` fields for incident triage, plus typed dispatch reason codes
- POST /router/embed
  - Accepts normalized broker request shape for embeddings
  - Supports `project_id` for project-specific policy overrides
  - Applies `task_overrides.embed` and `project_overrides.<project>.task_overrides.embed` when configured
  - Uses the same policy-chain dispatch and fallback model
  - Local dispatch uses the dedicated `embed_active_model` vLLM slot on port 8503 and requires authoritative `embeddings` capability metadata
  - Fresh-install catalogs include capability-scoped `text-embedding-3-small` fallbacks through OpenRouter paid and direct OpenAI lanes
  - Decision records include deterministic `attempt_trace` and `fallback_summary` fields for incident triage, plus typed dispatch reason codes
- POST /router/route-test
  - Dry-run route resolution utility that returns strategy, candidate chain, selected models per lane, and cooldown/lifecycle hints
  - Includes `strategy_resolution`, `chain_resolution`, and `policy_context` for deterministic strategy-source, chain-source, strict-provider task-constraint, service-tier inspection, and dynamic-ranking diagnostics (`dynamic_ranking_*` + per-lane score rows)
  - Optional `execute_first=true` runs a lightweight execution against the first eligible candidate for validation
  - Execution diagnostics now include resolved local backend and local slot mode when local lane is selected
- GET /router/health
  - Router config load status plus recent decision-log signal
  - Includes cached OpenRouter catalog, rankings freshness, and manual free-candidate counts
- GET /router/last-decisions?limit=20
  - Returns recent routing decision records
- GET /router/decision-traces?limit=40&compact=true
  - Returns recent route traces with task-policy context (`task_type`, `strategy_source`, `policy_context`) and backend/task/provider summary counters for operator dashboards
  - Summary includes deterministic fallback visibility fields (`by_reason_code`, `with_selected_fallback`)
- GET /router/usage-summary?limit=200
  - Aggregates usage logs by provider across the selected window
- GET /router/budget-state
  - Returns budget guardrail thresholds, current day and month spend totals, and warning or exceeded flags
- GET /router/queue-state
  - Shows free-tier queue depth, pending and expired counts, and recent queued items
- GET /router/fallback-stats?limit=500
  - Aggregates fallback and error patterns from recent routing decisions
  - Includes `by_reason_code` and `with_selected_fallback` for deterministic fallback-path analysis
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
- `wait` blocks the current request until the priority queue owns limiter capacity or `max_queue_wait_ms` expires; timeout removes the entry before fallback/failure
- Free-tier queue scheduling is priority-aware (`interactive`, `batch`, `evaluation`) and can evict lower-priority queued entries when at capacity
- Rate-limited or retryable provider failures can place provider-model pairs into temporary cooldown windows
- OpenRouter cooldown behavior is policy-tunable via `openrouter.cooldown_seconds_*` settings and `openrouter.auth_error_manual_review_threshold`
- OpenRouter failure state now captures `last_error_status_code`, `last_error_provider_code`, and `last_error_provider_type`
- Cooldown and limiter state are persisted under the `provider_model_state` and `provider_rate_limits` SQLite sections
- OpenRouter upstream model metadata can be refreshed and cached to harden free-tier routing decisions
- OpenRouter rankings enrichment is optional and is only fetched when operators call refresh or discovery with include_rankings enabled
- Automatic free-tier cycling now skips models marked in provider_model_state as disabled_until_manual_review or exclude_from_free_rotation
- Provider-model lifecycle now tracks promotion states (`discovered`, `candidate`, `smoke_passed`, `active`, `quarantined`, `retired`) and rolling failure-window metrics (`failure_count_24h`, `failure_count_7d`)
- Manual discovery results are stored under the `openrouter_free_candidates` SQLite section
- The openrouter.free lane only consults manually activated active_ids after curated free entries are exhausted
- Auto smoke checks support capability validation for structured outputs and tools when those requirements are requested during discovery

## Pricing and Budget Guardrails

- Provider catalog entries in config/provider_models.json can include pricing metadata keys such as input_cost_usd_per_1k and output_cost_usd_per_1k
- Router decisions can record estimated request cost when pricing metadata is present
- Runtime spend tracking is stored in SQLite under `budget_state` and `spend_logs`
- Policy budget controls live under config/provider_policies.json budget:
  - daily_usd_limit
  - monthly_usd_limit
  - per_request_usd_limit
  - warn_threshold_pct
  - hard_fail_on_budget_exceeded
  - providers map to enable guardrails per provider
- `model_preferences.preferred_model` is accepted only when the exact model is enabled, capability-compatible, and present in a policy-permitted candidate lane; the normal budget guardrail still runs

## Local Evaluation Pipeline

- Local evaluation artifacts are stored in SQLite runtime sections under:
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
- kind: train, merge, convert, convert_hf_exl2, or convert_merged_exl2
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

Managed conversion kinds also include:
- `convert_hf_exl3`
- `convert_merged_exl3`

Notes:
- Jobs are launched as local subprocesses rooted at the project directory
- Generic job records and process identity are persisted in SQLite
- On startup, live Linux jobs are reattached using verified boot/start/cmdline identity plus pidfd; missing or changed processes are marked interrupted
- Job paths and repository identifiers are constrained to configured managed roots before launch
- Cancellation uses the live managed process handle (pidfd on Linux when available), not the numeric PID copied into response state
- Logs are written to run/logs/
- Managed conversion run/artifact metadata is persisted in SQLite runtime sections; legacy JSON is imported once during schema initialization

## Managed EXL2 Conversion

- POST /conversions/exl2
  - Starts managed EXL2 conversion from either source type:
    - `source_type=huggingface_repo` with `repo_id`
    - `source_type=merged_local_model` with `model_key` (optional `source_model_dir`, `output_dir` overrides)
  - Body: { source_type, repo_id?, model_key?, source_model_dir?, output_dir?, bits, groupsize, force?, base_models_dir?, webui_models_dir?, exllama_root? }
- GET /conversions/exl2/jobs
  - Lists persisted EXL2 conversion runs for both HF and merged-local source types
- GET /conversions/exl2/jobs/{job_id}
  - Returns one persisted conversion run with log tail
- GET /conversions/exl2/artifacts
  - Lists persisted EXL2 conversion artifacts and metadata
- GET /conversions/exl2/artifacts/{artifact_id}
  - Returns one converted artifact record

Persisted EXL2 metadata fields include source type and source id/hash, bits, groupsize, output directory/model dir, timestamps, detected format/loader, preservation checks (`tokenizer` artifacts + chat-template continuity), and catalog sync result.

## Managed EXL3 Conversion

- POST /conversions/exl3
  - Starts managed EXL3 conversion from either source type:
    - `source_type=huggingface_repo` with `repo_id`
    - `source_type=merged_local_model` with `model_key` (optional `source_model_dir`, `output_dir` overrides)
  - Body: { source_type, repo_id?, model_key?, source_model_dir?, output_dir?, bits, groupsize?, force?, base_models_dir?, webui_models_dir?, exllama_root?, convert_script? }
  - Returns `503` with toolchain diagnostics when ExLlamaV3 conversion tooling is not installed or not configured (`EXLLAMA_V3_ROOT` or `EXL3_CONVERT_SCRIPT`)
  - Host validation note: first successful managed run completed on 2026-05-19 (`job_id=aa060e774ece`, `artifact_id=6dab1cbca58468eb`)
- GET /conversions/exl3/jobs
  - Lists persisted EXL3 conversion runs for both HF and merged-local source types
- GET /conversions/exl3/jobs/{job_id}
  - Returns one persisted EXL3 conversion run with log tail
- GET /conversions/exl3/artifacts
  - Lists persisted EXL3 conversion artifacts and metadata
- GET /conversions/exl3/artifacts/{artifact_id}
  - Returns one converted EXL3 artifact record

Persisted EXL3 metadata follows the same run/artifact discipline as EXL2, including source identity, bits, output path/model dir, timestamps, detected format/loader, preservation checks, and catalog sync result.

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
- The effective runtime model directory may differ from the default in `llm_manager/server.py` when overridden by the systemd service environment.
