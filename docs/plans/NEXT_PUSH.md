<!--
id: PLAN-NEXT-PUSH
version: 1.4
last_updated: 2026-05-14
title: Next Push Recommendations
purpose:
  Fresh status scan, near-term priorities, and TGW WebUI direction for the next implementation push.
-->
# LLM Manager - Next Push

## Docs sweep update (2026-05-14)

- stale conversion/catalog checkboxes were updated in `CHECKLIST.md` and `ROADMAP.md`
- managed EXL2 milestones are now split into completed HF-source scope vs pending merged-source scope

## Implementation update (2026-05-14)

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

Next queued item:
- item 5: provider admin and operator UI controls

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
- total tracked tasks: 53
- complete: 34
- pending: 19
- completion: 64.2%

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

5. [ ] Provider admin and operator UI controls
- Add curated-provider admin controls and improve route/backend visibility in dashboard workflows.

6. [ ] EXL2 Phase 1 closeout
- Extend managed EXL2 conversion to merged local model sources and lock tokenizer/chat-template preservation checks.

## Biggest remaining value gaps

1. Cross-provider strategy hardening
- Tighten deterministic behavior and observability for `local_first`, `free_first`, `paid_first`, `best_available`, and `strict_provider`.

2. OpenRouter resilience hardening
- Implement deeper error normalization + cooldown semantics + curated free auto-cycling behavior.
- Highest broker reliability gain.

3. vLLM and TabbyAPI lane first-classization
- Make AWQ/GPTQ prefer vLLM where supported and expose capabilities clearly.

4. EXL3 conversion path (Phase 2)
- Extend managed conversion model from EXL2 to EXL3 with matching metadata discipline.

5. Canonical layout ops doc package
- Finalize `/srv/2bananas/engines` structure/runbook docs and migration checks.

## Suggested immediate execution package (next 1-2 pushes)

Push 1:
- route-decision traces expanded with task-type policy context
- provider admin and operator UI controls for curated model governance and route/backend visibility
- docs update for provider admin and operator workflows

Push 2:
- OpenRouter capability-aware smoke checks (JSON schema + tools)
- vLLM/Tabby lane capability surfacing and fallback policy hardening

If both pushes land cleanly, broker maturity and operator confidence increase significantly while setting up Phase-3 backend goals.
