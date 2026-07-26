# LLM Manager - Roadmap

> Consolidated: 2026-07-26 | Latest evidence: docs/reviews/2026-07-26-foundation-review.md

## Current Status
LLM Manager is a production-like local control plane and universal broker across
TGW, TabbyAPI, vLLM, direct llama.cpp, OpenRouter, and OpenAI. The July 26
foundation pass added configuration-aware supervision, capability readiness, and
known-dead local-lane exclusion after a 4,027-restart incident. It is not yet
production-ready because privileged lifecycle/governance endpoints lack an
authentication boundary and backup/restore plus wider contract evidence remain.

Immediate roadmap order:

1. Preserve the stabilized launcher/unit/readiness contract and monitor it under normal use.
2. Add scoped lifecycle/governance authorization before broader API exposure.
3. Complete SQLite integrity/backup/restore drills and consumer contract fixtures.
4. Raise coverage progressively, then validate a Python 3.12 migration against every backend.

WebUI overhaul status:
- The dedicated overhaul is complete and retired.
- See [docs/plans/retired/WEBUI_OVERHAUL_CLOSEOUT.md](docs/plans/retired/WEBUI_OVERHAUL_CLOSEOUT.md) for final validation and retirement notes.
- Future UI work continues in this roadmap and [docs/plans/CHECKLIST.md](docs/plans/CHECKLIST.md).

Source-plan status update (2026-05-15):
- Evaluation, OpenRouter free-model discovery, and provider-router source plans are now retired under `docs/plans/retired/`.
- Their delivered scope remains tracked in this roadmap and [docs/plans/CHECKLIST.md](docs/plans/CHECKLIST.md).
- The remaining provider-router gap (cost-aware dynamic ranking) is tracked in the active backlog at [docs/plans/NEXT_PUSH.md](docs/plans/NEXT_PUSH.md).

## Phase 1: Foundation And Abstractions (Quick Wins)
- [x] Extend model inspection from loader recommendation to backend recommendation and fallback backends. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Add a normalized backend field to slot or switch configuration. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Introduce launcher wrappers for TGW, vLLM, and TabbyAPI. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Keep the existing engine-management API and dashboard controls stable while backend abstraction is introduced. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Standardize and document the canonical engine and model directory layout under `/srv/2bananas/engines`. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_ (documented in `docs/guides/RB-ENGINES-LAYOUT.md` with migration and validation checklist)
- [x] Align deployed systemd engine units with the documented launcher path and add startup guardrails for stale ExLlamaV2 JIT locks and unclean TGW restarts. - _source: local runtime troubleshooting (2026-04-28)_ (launcher guardrails now clean stale TGW port owners and stale ExLlama lock files; smoke-validated on 2026-05-19)
- [x] Add provider catalog config for local, OpenRouter free, OpenRouter paid, and OpenAI models. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_
- [x] Add request normalization and response normalization for brokered inference. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (chat/completions/embed contract and normalized responses)
- [x] Implement a unified broker entrypoint for routed chat requests. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (`POST /router/chat` adapter-dispatch slice)
- [x] Add secret configuration for provider API keys and default routing policy. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_
- [x] Add persistent runtime state for provider health, cooldowns, rate limits, queues, and evaluation artifacts. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md, docs/plans/retired/EVALUATION_PLAN.md_ (evaluation_suites/runs/reports state now persisted)
- [x] Add an operator recovery runbook for GPU-driver wedges and uninterruptible TGW processes, including reboot criteria and sequential post-reboot validation when only one local slot is expected to run at a time. - _source: local runtime troubleshooting (2026-04-28)_ (documented in `docs/CLI_SYSOP_GUIDE.md` with manual-review/quarantine recovery and sequential lane bring-up)

## Phase 2: Local Backends And Remote Providers (Near-term)
- [x] Add vLLM slot launching and unit support. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_ (launcher wrapper plus backend-aware slot base resolution are now wired into routing and engine status surfaces)
- [x] Route AWQ and GPTQ models to vLLM by default where supported. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_ (`/switch` now auto-applies recommended vLLM backend for compatible model kinds when backend is omitted)
- [x] Add optional embeddings and classification-capable slot metadata. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_ (authoritative v2 capability lists plus dedicated vLLM embedding slot)
- [x] Implement OpenRouter and OpenAI provider adapters. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (chat adapter slice)
- [x] Support explicit provider selection and explicit model selection from client projects. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (`provider_preferences.preferred_provider` + `model_preferences.preferred_model` contract and strict-provider execution path are implemented)
- [x] Add curated free and paid provider catalogs editable without code changes. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (config-driven catalogs + governance endpoints)
- [x] Add a shared free-tier rate limiter capped at 20 requests per minute. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (OpenRouter free-tier local limiter slice)
- [x] Add free-tier queueing with configurable wait, fail-fast, paid-upgrade, or local-fallback behavior. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (initial queue behavior slice)
- [x] Promote existing EXL2 conversion into a first-class llm-manager workflow for raw HF models. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_ (managed `/conversions/exl2/*` API flow)
- [x] Extend managed EXL2 conversion workflow to merged local models. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_ (`source_type=merged_local_model` support in managed conversion API)
- [x] Preserve tokenizer metadata and chat templates during EXL2 conversion so converted instruct models retain correct prompt formatting after deployment. - _source: local runtime troubleshooting (2026-04-28)_ (tokenizer/chat-template preservation checks now persisted in conversion metadata)
- [x] Persist conversion metadata in the model catalog. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_ (runtime state + catalog surfacing in `/models` and `/providers/models`)
- [x] Add admin API support to submit, monitor, and inspect conversion jobs. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_ (`/conversions/exl2/*`)

## Phase 3: ExLlama Lane And Resilient Routing (Mid-term)
- [x] Add a TabbyAPI launcher path. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_ (launcher wrapper shipped and integrated into backend-aware slot lane resolution)
- [x] Treat EXL2 and EXL3 as first-class backends through the ExLlama lane. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md, docs/plans/EXLLAMA_CONVERSION_PLAN.md_ (local lane dispatch and endpoint selection now honor slot backend and slot model alias instead of chat-only routing)
- [x] Add an EXL3 conversion path once the ExLlamaV3 toolchain is installed and validated on the host. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_ (host toolchain install + first successful managed run complete: `job_id=aa060e774ece`, `artifact_id=6dab1cbca58468eb`, preservation checks `ok=true`)
- [x] Add backend-native TabbyAPI load/unload operations with transactional symlink/restart fallback. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_ (native lifecycle attempts plus serialized rollback-safe fallback are implemented; steady-traffic host validation remains)
- [x] Refresh upstream OpenRouter metadata and intersect it with the curated free allowlist. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_
- [x] Normalize OpenRouter errors into actionable routing states. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (status/body-aware classification with provider code/type metadata)
- [x] Persist cooldowns, degraded states, and manual-review flags for provider models. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_
- [x] Auto-cycle across curated free models before failing a request. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (with capability-aware smoke checks for tools and structured outputs)
- [x] Persist richer OpenRouter discovery ranking fields and lifecycle/smoke evidence surfacing for candidate inspection (`top_weekly_rank`, `category_ranks`, transition/smoke evidence). - _source: docs/plans/retired/OPENROUTER_FREE_MODEL_DISCOVERY_PLAN.md, docs/plans/NEXT_PUSH.md_
- [x] Support cross-provider strategies such as local_first, free_first, paid_first, best_available, and strict_provider. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (service-tier-aware chain resolution, strict-provider lane targeting, typed fallback reason codes)

## Phase 4: Evaluation, Admin UX, And Observability (Future)
- [x] Add evaluation suite schemas, storage, and execution runner. - _source: docs/plans/retired/EVALUATION_PLAN.md_ (initial local evaluation pipeline implemented)
- [x] Add async local evaluation queueing with priority lanes for larger test batches. - _source: docs/plans/retired/EVALUATION_PLAN.md_ (interactive/batch/evaluation queue slice implemented)
- [x] Add per-priority concurrency caps and multi-worker execution controls for local evaluation queues. - _source: docs/plans/retired/EVALUATION_PLAN.md_ (worker/cap controls implemented)
- [x] Run evaluation suites across local and remote candidate models. - _source: docs/plans/retired/EVALUATION_PLAN.md_ (mixed local/OpenRouter/OpenAI candidate execution implemented)
- [x] Generate raw results, summary reports, and compact comparison artifacts for secondary LLM adjudication. - _source: docs/plans/retired/EVALUATION_PLAN.md_ (initial compare-compact endpoint implemented)
- [x] Allow historical suites to be rerun against newly added models. - _source: docs/plans/retired/EVALUATION_PLAN.md_ (suite rerun endpoint implemented for local pipeline)
- [x] Add threshold-based pass/fail gating and filtered evaluation-run listing for faster triage loops. - _source: docs/plans/retired/EVALUATION_PLAN.md_ (case/suite thresholds and `/router/evaluations` filters implemented)
- [x] Document routed inference usage and evaluation-suite submission for external projects. - _source: docs/plans/retired/EVALUATION_PLAN.md, docs/plans/retired/PROVIDER_ROUTER_PLAN.md, docs/TODO/README.md_ (`docs/guides/EXTERNAL_INTEGRATION.md` added)
- [x] Add router health, decision logs, fallback stats, queue state, and evaluation summary endpoints. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md, docs/plans/retired/EVALUATION_PLAN.md_ (evaluation summary slice implemented)
- [x] Expand route decision traces with task-policy context and backend/task mix summaries for operator triage. - _source: docs/plans/NEXT_PUSH.md_ (`GET /router/decision-traces` + enriched decision metadata)
- [x] Show slot backend, model format, backend recommendation, provider health, and fallback chains in the UI. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md, docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (Operations cards now surface backend/model/recommendation context and backend-aware test gating; Router Ops already surfaces provider health and fallback chains)
- [x] Add a lightweight dashboard evaluation operations panel for queue and report visibility. - _source: docs/plans/retired/EVALUATION_PLAN.md_ (initial Evaluation Ops panel in `web/`)
- [x] Add admin controls for enabling, disabling, prioritizing, and testing curated provider models. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (curated summary/update APIs + dashboard quick-admin grid)

## Phase 5: Governance, Spend, And Policy Controls (Future)
- [x] Record token usage by provider and model. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_
- [x] Track approximate spend for paid providers. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_
- [x] Add per-project routing policy overrides. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_
- [x] Add per-task-type routing policy overrides for chat, completion, and embed. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (`task_overrides` + `project_overrides.<project>.task_overrides`)
- [x] Add budget guardrails for paid routing. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_
- [x] Add cost-aware dynamic model ranking within a strategy (beyond fixed lane order). - _source: docs/plans/NEXT_PUSH.md, docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (policy-driven dynamic lane ranking implemented with cost/availability/quality weights and route-test chain diagnostics)
- [x] Add longer-horizon retention and audit controls for router/governance/lifecycle history. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md_ (`retention` policy config + `/providers/retention-state`)
- [x] Report whether a cheaper model lane is already sufficient for a task. - _source: docs/plans/retired/PROVIDER_ROUTER_PLAN.md, docs/plans/retired/EVALUATION_PLAN.md_ (`/router/lane-sufficiency-report` + dashboard lane sufficiency panel)

## Parking Lot
Items not yet prioritized:
- Direct llama.cpp integration is complete; keep upstream CLI compatibility in
  the normal backend-registry maintenance path.
- A lightweight raw-Transformers fallback backend derived from existing experiments. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- Turning llm-manager into a broader local AI orchestrator for ComfyUI or Home Assistant-adjacent workflows. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- Treating FlashInfer as a managed engine rather than an acceleration capability. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
