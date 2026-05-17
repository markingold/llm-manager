<!--
id: PLAN-NEXT-PUSH
version: 2.2
last_updated: 2026-05-17
title: Next Push Recommendations
purpose:
  Fresh status scan, near-term priorities, and TGW WebUI direction for the next implementation push.
-->
# LLM Manager - Next Push

## Docs sweep update (2026-05-17)

- stale conversion/catalog checkboxes were updated in `CHECKLIST.md` and `ROADMAP.md`
- managed EXL2 milestones are now marked complete for HF + merged-local source scope, including tokenizer/chat-template preservation checks
- CLI runbook now includes an incident workflow for curated quick-admin changes + decision trace triage
- retired source plans moved under `docs/plans/retired/` (`EVALUATION_PLAN.md`, `OPENROUTER_FREE_MODEL_DISCOVERY_PLAN.md`) and consolidated source references were updated
- retired source plans now also include `PROVIDER_ROUTER_PLAN.md`, with its remaining dynamic-ranking gap moved into this active backlog
- backend-aware frontend operation gating is now complete in Operations UI (slot backend/model/recommendation surfacing + capability-aware test gating)
- recovery runbook hardening is now complete in `docs/CLI_SYSOP_GUIDE.md` with explicit manual-review/quarantine recovery and GPU/TGW wedge reboot criteria
- canonical `/srv/2bananas/engines` layout documentation is now complete in `docs/guides/RB-ENGINES-LAYOUT.md` with migration and verification checklists
- cost-aware dynamic model ranking is now complete with policy-tunable lane scoring and chain-resolution diagnostics

## Implementation update (2026-05-17)

Completed items from the locked queue:
- item 1: model-lane sufficiency reporting is now implemented end-to-end
  - API endpoint: `GET /router/lane-sufficiency-report`
  - dashboard panel: Evaluation -> Lane Sufficiency
  - docs and plan trackers updated to mark this item complete
- item 2: per-task-type policy overrides (`chat`, `completion`, `embed`) are now implemented in strategy and lane-chain resolution
  - policy schema now supports `task_overrides` at global and `project_overrides.<project>.task_overrides` scopes
  - task-aware diagnostics now supported in `POST /providers/policies/test` and `POST /router/route-test`
- item 3: retention and audit controls are now implemented for longer-horizon routing audit and lifecycle/failure history
  - policy schema now supports configurable `retention` controls in `config/provider_policies.json`
  - runtime retention controls now govern request/usage/spend/governance history windows and lifecycle failure/promotion history depth
  - API endpoint added: `GET /providers/retention-state`
- item 4: OpenRouter discovery polish is now implemented
  - richer ranking fields (`top_weekly_rank`, `category_ranks`) now persist in catalog/discovery candidate payloads when available
  - lifecycle/smoke evidence is now surfaced on candidate rows (`recent_promotion_transitions`, `recent_smoke_checks`, `lifecycle_evidence`)
  - smoke evidence retention is now configurable via `retention.smoke_checks_max`
- item 5: provider admin and operator UI controls are now implemented
  - curated model quick-admin API and workflow added: `GET /providers/models/curated-summary`, `POST /providers/models/curated-entry`
  - dashboard governance panel now supports filtered curated-row updates (enabled/priority/backend/notes) with actor/reason audit fields
  - route/backend visibility expanded in Router Ops with decision trace summaries and backend/task/provider mix pills
- item 6: EXL2 Phase 1 closeout is now implemented
  - managed conversion now supports merged local sources via `source_type=merged_local_model` in `POST /conversions/exl2`
  - conversion jobs list now includes both HF and merged-local managed runs
  - conversion run/artifact metadata now includes tokenizer/chat-template preservation checks for post-conversion validation
- route decision trace expansion is now implemented
  - routing decisions now capture task-policy context fields (`task_type`, `strategy_source`, `policy_context`)
  - compact trace endpoint added: `GET /router/decision-traces`
- item 7: cross-provider strategy hardening is now completed
  - deterministic chain resolution now includes explicit chain source metadata and service-tier-aware lane selection
  - strict-provider behavior now supports lane-level targets (`openrouter.free`, `openrouter.paid`, `openai`, `local`)
  - strict-provider lane targets are now task-type constrained (`embed` excludes `openrouter.free` and resolves strict OpenRouter embed requests to `openrouter.paid`)
  - route traces now emit typed dispatch reason codes (`dispatch_rate_limited`, `dispatch_auth_error`, etc.) and provider-block skip reasons when auth failures occur
  - `POST /providers/policies/test` and `POST /router/route-test` now expose `chain_resolution` in addition to `strategy_resolution`
  - Router Ops now includes deterministic fallback reason and selected-fallback ratio pills for incident triage
- OpenRouter resilience hardening is now completed
  - OpenRouter adapter error normalization now parses HTTP status + provider error body fields and emits richer normalized types (`context_too_large`, `provider_timeout`, etc.)
  - runtime failure state now stores `last_error_status_code` and provider codes/types for incident triage
  - OpenRouter cooldown/manual-review thresholds are now policy-configurable (`cooldown_seconds_*`, `auth_error_manual_review_threshold`)
  - smoke checks are now capability-aware for structured outputs and tools (including schema/tool-call validation evidence)
- item 8: vLLM and TabbyAPI lane first-classization is now completed
  - local dispatch now resolves slot mode from selected local model alias and routes to backend-aware slot API bases instead of always using the chat slot base
  - backend-specific slot base env overrides are now supported (`LLM_*_API_BASE_TGW|VLLM|TABBYAPI`) with generic base fallback
  - `/switch` now auto-applies recommended backend for vLLM/Tabby-oriented model kinds when backend is omitted, while preserving explicit backend requests
  - `/models`, `/engines/status`, `/health`, and `/test-*` now expose/use backend-aware slot endpoint resolution
  - route-test execution diagnostics now include resolved local backend and local slot mode
- item 9: backend-aware frontend operation gating is now completed
  - Operations engine cards now surface slot backend, active model format (`kind`), and inspector backend recommendation/fallback context
  - slot test actions are now gated by backend/model compatibility and resolved endpoint availability with explicit reason hints
  - quick prompt test controls now mirror slot-level capability gating and disable unsupported test paths
- item 10: recovery runbook hardening is now completed
  - CLI runbook now documents explicit procedures for clearing provider `manual_review` and `quarantined` states via provider-model flag and curated-entry APIs
  - runbook now defines GPU-driver wedge and uninterruptible TGW process reboot criteria plus sequential single-lane post-reboot validation probes
- item 11: canonical layout ops doc package is now completed
  - canonical `/srv/2bananas/engines` filesystem contract is now documented with source-of-truth model storage and active-slot symlink expectations
  - migration runbook now includes preflight checks, unit wiring validation, sequential lane restart checks, and final contract verification
- item 12: cost-aware dynamic model ranking is now completed
  - policy schema now supports `dynamic_ranking` controls (`enabled`, strategy allowlist, cost/availability/quality weights, token estimate defaults)
  - candidate chain resolution now computes and applies weighted lane ranking (cost + availability + quality) when enabled
  - `POST /providers/policies/test` and `POST /router/route-test` now surface ranking diagnostics in `chain_resolution` and `policy_context`

Next queued item:
- EXL3 conversion path (Phase 2)

## Implementation update (2026-05-09)

Completed in this push:
- TGW WebUI decoupling slice completed:
  - slot launch path now defaults to API-only (`run/engine_launcher.py` forwards `--no-webui`)
  - standalone TGW WebUI control/status remains independent under `/engines/tgw-webui/*`
- conversion pipeline promotion completed for managed EXL2:
  - managed endpoints added: `POST /conversions/exl2`, `GET /conversions/exl2/jobs`, `GET /conversions/exl2/jobs/{job_id}`, `GET /conversions/exl2/artifacts`, `GET /conversions/exl2/artifacts/{artifact_id}`
  - metadata persistence added in runtime state (`conversion_runs`, `conversion_artifacts`)
  - converted artifacts now surface in `/models` and `/providers/models`, and sync into `local.converted_models`

Previously completed in this sequence:
- automatic smoke-check and promotion path for discovered OpenRouter free candidates
- explicit promotion lifecycle states and transitions (`discovered`, `candidate`, `smoke_passed`, `active`, `quarantined`, `retired`)
- rolling failure-window metrics (`failure_count_24h`, `failure_count_7d`) used by scoring and quarantine/retirement logic
- per-project routing policy overrides in strategy and lane-chain resolution
- priority-aware free-tier queue scheduling for `interactive`, `batch`, and `evaluation`
- high-value diagnostic endpoints:
  - `POST /router/route-test`
  - `POST /providers/policies/test`
  - `GET /providers/openrouter/rate-limit-state`

## Completion status snapshot

Checklist baseline from `docs/plans/CHECKLIST.md`:
- total tracked tasks: 56
- complete: 46
- pending: 10
- completion: 82.1%

Interpretation:
- Core broker and operator capabilities are now established.
- Remaining work is concentrated in route-quality hardening, backend lane maturity, and EXL3 follow-through.

## Locked implementation order (active queue)

1. [x] Model-lane sufficiency reporting

2. [x] Per-task-type policy overrides
- Add policy overrides by task type (`chat`, `completion`, `embed`) in addition to project defaults.

3. [x] Retention and audit controls
- Add longer-horizon routing audit retention and lifecycle/failure-history retention controls.

4. [x] OpenRouter discovery polish
- Persist richer ranking fields (`top_weekly_rank`, `category_ranks`) and add lifecycle/smoke evidence surfacing.

5. [x] Provider admin and operator UI controls
- Add curated-provider admin controls and improve route/backend visibility in dashboard workflows.

6. [x] EXL2 Phase 1 closeout
- Extend managed EXL2 conversion to merged local model sources and lock tokenizer/chat-template preservation checks.

7. [x] Cross-provider strategy hardening
- Deterministic strategy behavior and fallback observability hardened across `local_first`, `free_first`, `paid_first`, `best_available`, and `strict_provider`.

## Biggest remaining value gaps

1. EXL3 conversion path (Phase 2)
- Extend managed conversion model from EXL2 to EXL3 with matching metadata discipline.

2. TabbyAPI lane lifecycle decision
- Decide whether TabbyAPI switching remains symlink-based or gains backend-native load/unload operations.

3. Systemd launcher alignment and startup guardrails
- Align deployed engine units with launcher expectations and add guardrails for stale ExLlamaV2 JIT locks and unclean TGW restarts.

## Suggested immediate execution package (next 1-2 pushes)

Push 1:
- EXL3 conversion path bootstrap + metadata parity checks

Push 2:
- optional TabbyAPI backend-native model load/unload decision and prototype
- systemd launcher alignment + startup guardrail implementation and validation

If both pushes land cleanly, broker maturity and operator confidence increase significantly while setting up Phase-3 backend goals.
