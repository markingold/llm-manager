# LLM Manager - Roadmap

> Consolidated: 2026-03-29 | Sources: docs/plans/BACKEND_ARCHITECTURE_PLAN.md, docs/plans/EXLLAMA_CONVERSION_PLAN.md, docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md, docs/TODO/README.md

## Current Status
LLM Manager is currently a production control plane for slot-based local serving on dual GPUs, centered on text-generation-webui, systemd-managed engine slots, model switching, inspection, training jobs, and a dashboard. The active plan is to evolve it into a universal LLM broker that can route across local backends, OpenRouter, and OpenAI while adding managed conversion, evaluation, and observability.

## Phase 1: Foundation And Abstractions (Quick Wins)
- [x] Extend model inspection from loader recommendation to backend recommendation and fallback backends. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Add a normalized backend field to slot or switch configuration. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Introduce launcher wrappers for TGW, vLLM, and TabbyAPI. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Keep the existing engine-management API and dashboard controls stable while backend abstraction is introduced. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [ ] Standardize and document the canonical engine and model directory layout under `/srv/2bananas/engines`. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Add provider catalog config for local, OpenRouter free, OpenRouter paid, and OpenAI models. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Add request normalization and response normalization for brokered inference. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (chat/completions/embed contract and normalized responses)
- [x] Implement a unified broker entrypoint for routed chat requests. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (`POST /router/chat` adapter-dispatch slice)
- [x] Add secret configuration for provider API keys and default routing policy. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Add persistent runtime state for provider health, cooldowns, rate limits, queues, and evaluation artifacts. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md_ (evaluation_suites/runs/reports state now persisted)

## Phase 2: Local Backends And Remote Providers (Near-term)
- [ ] Add vLLM slot launching and unit support. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [ ] Route AWQ and GPTQ models to vLLM by default where supported. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [ ] Add optional embeddings and classification-capable slot metadata. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [x] Implement OpenRouter and OpenAI provider adapters. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (chat adapter slice)
- [ ] Support explicit provider selection and explicit model selection from client projects. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [ ] Add curated free and paid provider catalogs editable without code changes. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [x] Add a shared free-tier rate limiter capped at 20 requests per minute. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (OpenRouter free-tier local limiter slice)
- [x] Add free-tier queueing with configurable wait, fail-fast, paid-upgrade, or local-fallback behavior. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_ (initial queue behavior slice)
- [ ] Promote existing EXL2 conversion into a first-class llm-manager workflow for raw HF models and merged local models. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_
- [ ] Persist conversion metadata in the model catalog. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_
- [ ] Add admin API or CLI support to submit, monitor, and inspect conversion jobs. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_

## Phase 3: ExLlama Lane And Resilient Routing (Mid-term)
- [ ] Add a TabbyAPI launcher path. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [ ] Treat EXL2 and EXL3 as first-class backends through the ExLlama lane. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md, docs/plans/EXLLAMA_CONVERSION_PLAN.md_
- [ ] Add an EXL3 conversion path once the ExLlamaV3 toolchain is installed and validated on the host. - _source: docs/plans/EXLLAMA_CONVERSION_PLAN.md_
- [ ] Decide whether TabbyAPI switching should remain symlink-based or gain backend-native load and unload operations. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- [ ] Refresh upstream OpenRouter metadata and intersect it with the curated free allowlist. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [ ] Normalize OpenRouter errors into actionable routing states. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [ ] Persist cooldowns, degraded states, and manual-review flags for provider models. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [ ] Auto-cycle across curated free models before failing a request. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [ ] Support cross-provider strategies such as local_first, free_first, paid_first, best_available, and strict_provider. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_

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
- [ ] Record token usage by provider and model. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [ ] Track approximate spend for paid providers. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [ ] Add per-project routing policy overrides. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [ ] Add budget guardrails for paid routing. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md_
- [ ] Report whether a cheaper model lane is already sufficient for a task. - _source: docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md_

## Parking Lot
Items not yet prioritized:
- Direct llama.cpp integration as a backend separate from TGW. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- A lightweight raw-Transformers fallback backend derived from existing experiments. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- Turning llm-manager into a broader local AI orchestrator for ComfyUI or Home Assistant-adjacent workflows. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
- Treating FlashInfer as a managed engine rather than an acceleration capability. - _source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md_
