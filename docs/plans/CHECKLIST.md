# LLM Manager - Master Checklist

> Consolidated: 2026-04-29

WebUI overhaul status update (2026-05-07):
- The dedicated WebUI overhaul tracker is complete and retired.
- Historical references:
	- [docs/plans/WEBUI_OVERHAUL_CHECKLIST.md](docs/plans/WEBUI_OVERHAUL_CHECKLIST.md)
	- [docs/plans/WEBUI_OVERHAUL_PLAN.md](docs/plans/WEBUI_OVERHAUL_PLAN.md)
	- [docs/plans/WEBUI_OVERHAUL_CLOSEOUT.md](docs/plans/WEBUI_OVERHAUL_CLOSEOUT.md)
- New UI work should be tracked in this master checklist and the roadmap below.

## Must Do (blocking or high-value)
- [x] Extend `api/model_inspector.py` to emit `recommended_backend` and `fallback_backends`. - effort: medium | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [x] Add backend selection to slot or switch configuration. - effort: small | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [x] Add normalized launcher wrappers for TGW, vLLM, and TabbyAPI under `run/`. - effort: medium | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [ ] Standardize and document the canonical engine and model directory layout under `/srv/2bananas/engines`, keeping llm-manager as the control plane rather than the asset store. - effort: medium | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [ ] Align deployed systemd engine units with the documented launcher path and add startup guardrails for stale ExLlamaV2 JIT locks and unclean TGW restarts. - effort: medium | source: local runtime troubleshooting 2026-04-28
- [x] Add provider model catalog config for local, OpenRouter free, OpenRouter paid, and OpenAI. - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md
- [x] Add provider secrets and default routing config to the application configuration model. - effort: small | source: docs/plans/PROVIDER_ROUTER_PLAN.md
- [x] Define a normalized router request and response contract for chat, completion, embed, and evaluation workflows. - effort: large | source: docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md (chat/completions/embed slices implemented)
- [x] Implement provider adapters for local, OpenRouter, and OpenAI. - effort: large | source: docs/plans/PROVIDER_ROUTER_PLAN.md (chat adapter slice implemented)
- [x] Add `POST /router/chat` as the first universal broker endpoint. - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md (initial dry-run broker slice)
- [x] Persist provider runtime state for health, cooldowns, rate limits, queue state, and evaluation artifacts. - effort: large | source: docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md (evaluation_suites/runs/reports state persisted)
- [x] Add a shared OpenRouter free-tier limiter capped at 20 requests per minute. - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md (local protective limiter slice implemented)
- [x] Add queueing for free-tier requests with configurable overflow behavior. - effort: large | source: docs/plans/PROVIDER_ROUTER_PLAN.md (wait/fail_fast/fallback_to_local/upgrade_to_paid slice implemented)
- [x] Add automatic smoke-check promotion and lifecycle state transitions for discovered OpenRouter free candidates. - effort: medium | source: docs/plans/OPENROUTER_FREE_MODEL_DISCOVERY_PLAN.md
- [x] Persist richer OpenRouter discovery ranking fields and lifecycle/smoke evidence surfacing (`top_weekly_rank`, `category_ranks`, transition/smoke evidence fields). - effort: medium | source: docs/plans/NEXT_PUSH.md, docs/plans/OPENROUTER_FREE_MODEL_DISCOVERY_PLAN.md
- [x] Promote EXL2 conversion into a first-class managed workflow for HF-format sources. - effort: medium | source: docs/plans/EXLLAMA_CONVERSION_PLAN.md (managed `/conversions/exl2/*` API flow is live)
- [ ] Extend managed EXL2 conversion workflow to merged local model sources. - effort: medium | source: docs/plans/EXLLAMA_CONVERSION_PLAN.md
- [ ] Ensure EXL2 conversion preserves tokenizer metadata and chat templates required for correct instruct prompting. - effort: medium | source: local runtime troubleshooting 2026-04-28
- [x] Persist conversion metadata so converted models can be cataloged and routed. - effort: medium | source: docs/plans/EXLLAMA_CONVERSION_PLAN.md (runtime state + `/models` and `/providers/models` surfacing)
- [ ] Add a TabbyAPI launcher and treat EXL2 and EXL3 as first-class ExLlama-backed lanes. - effort: large | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md, docs/plans/EXLLAMA_CONVERSION_PLAN.md
- [x] Add OpenRouter metadata refresh, error normalization, cooldown state, and automatic free-model cycling. - effort: large | source: docs/plans/PROVIDER_ROUTER_PLAN.md (including smoke-check promotion path, lifecycle states, and rolling failure windows)
- [x] Add controlled cross-provider fallback strategies such as `local_first`, `free_first`, `paid_first`, `best_available`, and `strict_provider`. - effort: large | source: docs/plans/PROVIDER_ROUTER_PLAN.md
- [x] Add evaluation suite schemas, storage, runner, and reporting endpoints. - effort: large | source: docs/plans/EVALUATION_PLAN.md (initial local evaluation endpoint and report endpoints implemented)
- [x] Write external-project documentation for routed inference and evaluation-suite submission. - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md, docs/TODO/README.md (`docs/guides/EXTERNAL_INTEGRATION.md` added)

## Should Do (improves quality)
- [ ] Add vLLM slot launching and make AWQ and GPTQ prefer vLLM where supported. - effort: medium | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [ ] Add slot metadata for embeddings, classification, structured output, tool calling, and multimodal capabilities. - effort: medium | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [x] Add curated model admin endpoints for catalogs, policies, state, and refresh operations. - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md
- [x] Add high-value router/provider diagnostic endpoints for policy resolution, route testing, and OpenRouter limiter state (`/providers/policies/test`, `/router/route-test`, `/providers/openrouter/rate-limit-state`). - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md
- [ ] Document recovery for GPU-driver wedges and uninterruptible TGW engine processes, including reboot criteria and sequential single-lane validation steps. - effort: small | source: local runtime troubleshooting 2026-04-28
- [x] Add router observability endpoints for health, last decisions, fallback stats, usage summaries, queue state, and evaluation summaries. - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md (evaluation summary endpoint implemented)
- [ ] Surface backend, model format, and recommendation data in the dashboard. - effort: medium | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [ ] Hide or disable unsupported operations per backend in the UI. - effort: small | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [ ] Add admin controls for enabling, disabling, prioritizing, and testing curated provider models. - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md
- [x] Support rerunning historical evaluation suites against new candidate models. - effort: medium | source: docs/plans/EVALUATION_PLAN.md (initial suite rerun endpoint implemented for local pipeline)
- [x] Generate compact comparison artifacts suitable for ChatGPT or Copilot adjudication. - effort: medium | source: docs/plans/EVALUATION_PLAN.md (initial compare-compact endpoint implemented)
- [x] Add async evaluation queueing with configurable priority classes for local batch runs. - effort: medium | source: docs/plans/EVALUATION_PLAN.md (interactive/batch/evaluation queue slice implemented)
- [x] Add per-priority concurrency caps and multi-worker execution controls for local evaluation queues. - effort: medium | source: docs/plans/EVALUATION_PLAN.md (worker/cap controls implemented)
- [x] Add pluggable scoring checks for local evaluation cases (regex/json/contains/length) to improve prompt and parameter tuning decisions. - effort: medium | source: docs/plans/EVALUATION_PLAN.md (initial scoring plugin slice implemented)
- [x] Add threshold-based pass/fail gating and filtered evaluation run listing for tighter tuning audits. - effort: medium | source: docs/plans/EVALUATION_PLAN.md (case/suite thresholds and `/router/evaluations` filtering implemented)
- [x] Run local evaluation suites against mixed local and remote candidates with provider/lane-aware compare artifacts. - effort: medium | source: docs/plans/EVALUATION_PLAN.md (mixed candidate execution implemented)
- [x] Add provider and lane filters plus optional estimated-cost rollups for evaluation triage. - effort: medium | source: docs/plans/EVALUATION_PLAN.md, docs/plans/PROVIDER_ROUTER_PLAN.md (provider/lane/suite_pass filters and estimated cost summary fields implemented)
- [x] Add a lightweight dashboard panel for evaluation queue and report visibility. - effort: small | source: docs/plans/EVALUATION_PLAN.md (initial Evaluation Ops panel implemented)
- [x] Add priority-aware scheduling for free-tier router queue items with interactive/batch/evaluation classes. - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md
- [ ] Add EXL3 conversion once the ExLlamaV3 toolchain is installed and validated on the host. - effort: medium | source: docs/plans/EXLLAMA_CONVERSION_PLAN.md

## Nice To Have
- [ ] Add optional backend-native model load and unload support for the TabbyAPI lane. - effort: medium | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [ ] Add direct llama.cpp integration instead of relying on TGW for GGUF. - effort: large | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [ ] Add a lightweight raw-Transformers fallback backend if experiments justify it. - effort: large | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md
- [x] Add per-project routing overrides and budget guardrails for paid providers. - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md
- [x] Record spend and lane-sufficiency reporting to prove when cheaper models are good enough. - effort: medium | source: docs/plans/PROVIDER_ROUTER_PLAN.md, docs/plans/EVALUATION_PLAN.md (`/router/lane-sufficiency-report` + dashboard lane sufficiency panel)
- [ ] Add a broader orchestrator layer only if the project intentionally expands beyond LLM serving. - effort: large | source: docs/plans/BACKEND_ARCHITECTURE_PLAN.md

## Code Debt (from TODO/FIXME comments)
- [ ] 0 TODO or FIXME comments found across 0 files during consolidation scan. - effort: small | source: repo-wide grep on 2026-03-29
- [ ] Re-run the code-debt scan after implementation work starts so new debt is tracked in this checklist. - effort: small | source: consolidation process

## Final Upgrade Validation Gate
- [ ] After all upgrade-plan slices are complete, run the full end-to-end evaluation pipeline across all local models and lanes used on this host. If required model formats are missing, manually download and stage those model variants before final validation and sign-off.
