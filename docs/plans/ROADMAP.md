# LLM Manager - Roadmap

> Consolidated: 2026-04-29 | Sources: docs/plans/BACKEND_ARCHITECTURE_PLAN.md, docs/plans/EXLLAMA_CONVERSION_PLAN.md, docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md, docs/TODO/README.md, local runtime troubleshooting (2026-04-28)

## Current Status
LLM Manager is currently a production control plane for slot-based local serving on dual GPUs, centered on text-generation-webui, systemd-managed engine slots, model switching, inspection, training jobs, and a dashboard. The active plan is to evolve it into a universal LLM broker that can route across local backends, OpenRouter, and OpenAI while adding managed conversion, evaluation, and observability.

WebUI overhaul status:
- The dedicated overhaul is complete and retired.
- See [docs/plans/WEBUI_OVERHAUL_CLOSEOUT.md](docs/plans/WEBUI_OVERHAUL_CLOSEOUT.md) for final validation and retirement notes.
- Future UI work continues in this roadmap and [docs/plans/CHECKLIST.md](docs/plans/CHECKLIST.md).

## Phase 1: Foundation And Abstractions (Quick Wins)
- [x] Extend model inspection from loader recommendation to backend recommendation and fallback backends. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Add a normalized backend field to slot or switch configuration. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Introduce launcher wrappers for TGW, vLLM, and TabbyAPI. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Keep the existing engine-management API and dashboard controls stable while backend abstraction is introduced. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [ ] Standardize and document the canonical engine and model directory layout under `/srv/2bananas/engines`. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [ ] Align deployed systemd engine units with the documented launcher path and add startup guardrails for stale ExLlamaV2 JIT locks and unclean TGW restarts. - _source: local runtime troubleshooting (2026-04-28)_
- [x] Add provider catalog config for local, OpenRouter free, OpenRouter paid, and OpenAI models. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Add request normalization and response normalization for brokered inference. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (chat/completions/embed contract and normalized responses)
- [x] Implement a unified broker entrypoint for routed chat requests. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (`POST /router/chat` adapter-dispatch slice)
- [x] Add secret configuration for provider API keys and default routing policy. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Add persistent runtime state for provider health, cooldowns, rate limits, queues, and evaluation artifacts. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md_ (evaluation_suites/runs/reports state now persisted)
- [ ] Add an operator recovery runbook for GPU-driver wedges and uninterruptible TGW processes, including reboot criteria and sequential post-reboot validation when only one local slot is expected to run at a time. - _source: local runtime troubleshooting (2026-04-28)_

## Phase 2: Local Backends And Remote Providers (Near-term)
- [ ] Add vLLM slot launching and unit support. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [ ] Route AWQ and GPTQ models to vLLM by default where supported. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [ ] Add optional embeddings and classification-capable slot metadata. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Implement OpenRouter and OpenAI provider adapters. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (chat adapter slice)
- [ ] Support explicit provider selection and explicit model selection from client projects. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Add curated free and paid provider catalogs editable without code changes. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (config-driven catalogs + governance endpoints)
- [x] Add a shared free-tier rate limiter capped at 20 requests per minute. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (OpenRouter free-tier local limiter slice)
- [x] Add free-tier queueing with configurable wait, fail-fast, paid-upgrade, or local-fallback behavior. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (initial queue behavior slice)
- [x] Promote existing EXL2 conversion into a first-class llm-manager workflow for raw HF models. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_ (managed `/conversions/exl2/*` API flow)
- [ ] Extend managed EXL2 conversion workflow to merged local models. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_
- [ ] Preserve tokenizer metadata and chat templates during EXL2 conversion so converted instruct models retain correct prompt formatting after deployment. - _source: local runtime troubleshooting (2026-04-28)_
- [x] Persist conversion metadata in the model catalog. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_ (runtime state + catalog surfacing in `/models` and `/providers/models`)
- [x] Add admin API support to submit, monitor, and inspect conversion jobs. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_ (`/conversions/exl2/*`)

## Phase 3: ExLlama Lane And Resilient Routing (Mid-term)
- [ ] Add a TabbyAPI launcher path. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [ ] Treat EXL2 and EXL3 as first-class backends through the ExLlama lane. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md, docs/plans/EXLLAMA_CONVERSION_PLAN.md_
- [ ] Add an EXL3 conversion path once the ExLlamaV3 toolchain is installed and validated on the host. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_
- [ ] Decide whether TabbyAPI switching should remain symlink-based or gain backend-native load and unload operations. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Refresh upstream OpenRouter metadata and intersect it with the curated free allowlist. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Normalize OpenRouter errors into actionable routing states. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Persist cooldowns, degraded states, and manual-review flags for provider models. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Auto-cycle across curated free models before failing a request. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Persist richer OpenRouter discovery ranking fields and lifecycle/smoke evidence surfacing for candidate inspection (`top_weekly_rank`, `category_ranks`, transition/smoke evidence). - _source: docs/plans/OPENROUTER_FREE_MODEL_DISCOVERY_PLAN.md, docs/plans/NEXT_PUSH.md_
- [x] Support cross-provider strategies such as local_first, free_first, paid_first, best_available, and strict_provider. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_

## Phase 4: Evaluation, Admin UX, And Observability (Future)
- [x] Add evaluation suite schemas, storage, and execution runner. - _source: docs/plans/EVALUATION_PLAN.md_ (initial local evaluation pipeline implemented)
- [x] Add async local evaluation queueing with priority lanes for larger test batches. - _source: docs/plans/EVALUATION_PLAN.md_ (interactive/batch/evaluation queue slice implemented)
- [x] Add per-priority concurrency caps and multi-worker execution controls for local evaluation queues. - _source: docs/plans/EVALUATION_PLAN.md_ (worker/cap controls implemented)
- [x] Run evaluation suites across local and remote candidate models. - _source: docs/plans/EVALUATION_PLAN.md_ (mixed local/OpenRouter/OpenAI candidate execution implemented)
- [x] Generate raw results, summary reports, and compact comparison artifacts for secondary LLM adjudication. - _source: docs/plans/EVALUATION_PLAN.md_ (initial compare-compact endpoint implemented)
- [x] Allow historical suites to be rerun against newly added models. - _source: docs/plans/EVALUATION_PLAN.md_ (suite rerun endpoint implemented for local pipeline)
- [x] Add threshold-based pass/fail gating and filtered evaluation-run listing for faster triage loops. - _source: docs/plans/EVALUATION_PLAN.md_ (case/suite thresholds and `/router/evaluations` filters implemented)
- [x] Document routed inference usage and evaluation-suite submission for external projects. - _source: docs/plans/EVALUATION_PLAN.md, docs/plans/PROVIDER_ROUTER_PLAN.md, docs/TODO/README.md_ (`docs/guides/EXTERNAL_INTEGRATION.md` added)
- [x] Add router health, decision logs, fallback stats, queue state, and evaluation summary endpoints. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md_ (evaluation summary slice implemented)
- [ ] Show slot backend, model format, backend recommendation, provider health, and fallback chains in the UI. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md, docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Add a lightweight dashboard evaluation operations panel for queue and report visibility. - _source: docs/plans/EVALUATION_PLAN.md_ (initial Evaluation Ops panel in `web/`)
- [ ] Add admin controls for enabling, disabling, prioritizing, and testing curated provider models. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_

## Phase 5: Governance, Spend, And Policy Controls (Future)
- [x] Record token usage by provider and model. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Track approximate spend for paid providers. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Add per-project routing policy overrides. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Add per-task-type routing policy overrides for chat, completion, and embed. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (`task_overrides` + `project_overrides.<project>.task_overrides`)
- [x] Add budget guardrails for paid routing. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Add longer-horizon retention and audit controls for router/governance/lifecycle history. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (`retention` policy config + `/providers/retention-state`)
- [x] Report whether a cheaper model lane is already sufficient for a task. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md_ (`/router/lane-sufficiency-report` + dashboard lane sufficiency panel)

## Parking Lot
Items not yet prioritized:
- Direct llama.cpp integration as a backend separate from TGW. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- A lightweight raw-Transformers fallback backend derived from existing experiments. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- Turning llm-manager into a broader local AI orchestrator for ComfyUI or Home Assistant-adjacent workflows. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- Treating FlashInfer as a managed engine rather than an acceleration capability. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
