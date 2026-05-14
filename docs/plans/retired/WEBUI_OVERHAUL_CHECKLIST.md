<!--
id: PLAN-WEBUI-OVERHAUL-CHECKLIST
version: 1.2
last_updated: 2026-05-07
title: WebUI Overhaul Execution Checklist
purpose:
  Historical execution record for the completed WebUI overhaul, including final evidence and retirement status.
-->
# WebUI Overhaul Execution Checklist

Source plan: [docs/plans/WEBUI_OVERHAUL_PLAN.md](docs/plans/WEBUI_OVERHAUL_PLAN.md)

## How this checklist is used
- This file is retired and preserved as a historical execution artifact.
- Do not use this file for new implementation planning.
- For new work, use [docs/plans/CHECKLIST.md](docs/plans/CHECKLIST.md) and [docs/plans/ROADMAP.md](docs/plans/ROADMAP.md).

## Tracker status
- Overall status: completed
- Current active phase: Closed - retired to maintenance mode
- Last updated: 2026-05-07

## Closeout status
- [x] WebUI overhaul implementation complete
- [x] Overhaul plan retired to historical reference
- [x] Overhaul checklist retired to historical reference
- [x] Follow-on improvements moved to [docs/plans/ROADMAP.md](docs/plans/ROADMAP.md)
- [x] Final closeout notes recorded in [docs/plans/WEBUI_OVERHAUL_CLOSEOUT.md](docs/plans/WEBUI_OVERHAUL_CLOSEOUT.md)

## Evidence links
- Current dashboard entrypoint: [web/index.html](web/index.html)
- Current dashboard logic: [web/app.js](web/app.js)
- API implementation: [api/server.py](api/server.py)
- Overhaul plan: [docs/plans/WEBUI_OVERHAUL_PLAN.md](docs/plans/WEBUI_OVERHAUL_PLAN.md)

## Standards alignment gates (must remain green)

### Documentation standards gate
- [x] README updated when behavior or usage changes
- [x] [docs/API.md](docs/API.md) updated for added/changed/removed endpoints
- [x] Affected docs in [docs/guides/](docs/guides/) updated when workflows change
- [x] Ecosystem app handbook checked and updated if needed in [docs/2bananas/apps/](docs/2bananas/apps/)
- [x] If ports/services/global env keys change, corresponding docs updated:
- [x] [docs/2bananas/OPS_PORTS.md](docs/2bananas/OPS_PORTS.md)
- [x] [docs/2bananas/OPS_SERVICES.md](docs/2bananas/OPS_SERVICES.md)
- [x] [docs/2bananas/OPS_GLOBAL_ENV.md](docs/2bananas/OPS_GLOBAL_ENV.md)

### Logging and safety gate
- [x] New/changed API paths emit structured logs with required fields
- [x] HTTP request logs include request_id, method, path, status, duration_ms
- [x] No secrets/tokens/keys in logs or error payloads
- [x] No direct browser file writes for governance mutations
- [x] Governance mutations require server-side validation and explicit confirmation

### Project layout and dependency gate
- [x] No secrets or state files introduced under [web/](web/)
- [x] New dependencies added only when clearly justified
- [x] Any new dependency documented in README and requirements files as needed

## Phase A - Quick wins (already shipped)
- [x] Router Ops panel includes budget, fallback, queue, and catalog snapshot
- [x] OpenRouter metadata refresh action available in dashboard
- [x] Eval Ops includes recent completed run quick actions (Report/Compare/Raw)
- [x] Budget warning banner shown for warn/exceeded thresholds

## Phase A.5 - Governance API contracts (required before admin UI)

### Contract design
- [x] Define write contract for provider policies (update + validate-only)
- [x] Define write contract for provider model catalog (update + validate-only)
- [x] Define optimistic concurrency mechanism (version or etag)
- [x] Define audit record schema (ts, actor, reason, changed keys, outcome)
- [x] Define rollback contract and scope

### Backend implementation
- [x] Add provider policies write endpoint(s) in [api/server.py](api/server.py)
- [x] Add provider models write endpoint(s) in [api/server.py](api/server.py)
- [x] Add validate-only mode for both write paths
- [x] Add concurrency checks and conflict responses
- [x] Persist audit entries in runtime state store
- [x] Add rollback endpoint for most recent successful mutation

### Backend verification
- [x] Positive tests for valid policy/catalog updates
- [x] Negative tests for schema or value validation failures
- [x] Concurrency conflict test coverage
- [x] Audit entry generation verified
- [x] Rollback flow verified

## Phase B - Frontend foundation hardening

### Modularization
- [x] Split [web/app.js](web/app.js) into domain modules
- [x] Create shared API client module with consistent error handling
- [x] Create shared state/selectors utilities for dashboard sections
- [x] Keep parity behavior during refactor (no hidden regressions)

### Information architecture
- [x] Introduce route-level sections for read vs mutate surfaces
- [x] Keep one-click ops reachable from default landing view
- [x] Add clear nav model for Operations, Router, Evaluation, Providers/Budget, Jobs

### Smoke tests
- [x] Switch model + bounce path
- [x] Eval suite rerun path
- [x] OpenRouter catalog refresh path
- [x] Budget warn/exceeded UI visibility path

## Phase C - Evaluation UX

### Runs and filters
- [x] Runs table implemented with all API filters
- [x] Query-state persisted in URL params
- [x] Filter presets for common triage flows

### Drilldown and compare
- [x] Run detail drawer with summary and recommendations
- [x] Per-case outputs and failure visibility
- [x] Compare-compact renderer for side-by-side review

### Suite workflows
- [x] Baseline suite templates from UI
- [x] Suite rerun controls with async priority
- [x] Queue and worker state surfaced in table/detail context

## Phase D - Provider Governance UX

### Mutations UI
- [x] Policy editor wired to safe write contracts
- [x] Provider catalog editor wired to safe write contracts
- [x] Provider-model flags workflow (manual review reset, re-enable rotation)

### Safety UX
- [x] High-impact edits require confirmation modal
- [x] High-impact edits require reason text
- [x] Dry-run preview shown before apply
- [x] Conflict and validation errors are actionable in UI

### Audit and rollback UX
- [x] Governance audit trail view
- [x] Rollback action with clear scope and confirmation
- [x] Post-mutation success state shows audit reference

## Phase E - Full UI refresh
- [x] Consolidate component and style system
- [x] Improve visual hierarchy for incident triage
- [x] Ensure mobile and desktop operability
- [x] Final pass on performance and accessibility

## API parity checklist (must be complete)
- [x] /providers/openrouter/refresh
- [x] /providers/models (read)
- [x] /providers/policies (read)
- [x] /providers/models (write contract)
- [x] /providers/policies (write contract)
- [x] /router/budget-state
- [x] /router/last-decisions
- [x] /router/fallback-stats
- [x] /router/usage-summary
- [x] /router/queue-state
- [x] /router/evaluation-queue-state
- [x] /router/evaluation-summary
- [x] /router/evaluation-worker-config
- [x] /router/evaluations with full filter support in UI
- [x] /router/evaluations/{run_id} detail workflow in UI
- [x] /router/evaluations/{run_id}/report
- [x] /router/evaluations/{run_id}/compare-compact
- [x] /router/evaluation-suites CRUD and rerun in UI

## Definition-of-done outcomes (release gate)
- [x] Operators can complete common workflows without CLI fallback
- [x] Router decisions/failures are understandable without raw JSON
- [x] Budget guardrails are visible and actionable
- [x] Evaluation workflows are first-class (create, run, filter, compare)
- [x] Governance edits are safe-by-default (validate, confirm, audit, rollback)
- [x] 90%+ of daily operator tasks are dashboard-completable
- [x] Fallback incident triage to root cause in under 2 minutes
- [x] Docs and operator screenshots fully updated

## Work log

### 2026-03-29
- Initialized living checklist aligned to overhaul plan and 2bananas standards.
- Marked currently shipped Phase A and read-only API parity items as complete.
- Set active focus to Phase A.5 governance API contracts.
- Implemented governance write contracts in [api/server.py](api/server.py):
- PUT `/providers/models` and PUT `/providers/policies` with `validate_only` support
- optimistic version checks (`expected_version`) and conflict responses
- required reason text for apply and rollback operations
- rollback endpoints for latest successful pre-change snapshot
- governance audit trail and rollback snapshots persisted in runtime state
- Updated [docs/API.md](docs/API.md) for new governance endpoints and contract semantics.
- Updated ecosystem handbook API reference in [docs/2bananas/apps/llm-manager/API.md](docs/2bananas/apps/llm-manager/API.md) for governance write contracts.
- Verified governance contract behavior against current code with in-process FastAPI TestClient checks:
- `GET /providers/models` and `GET /providers/policies` include `version`.
- `PUT /providers/policies` validate-only returns success for valid document.
- `PUT /providers/policies` validate-only returns validation errors for invalid document.
- `PUT /providers/policies` with stale `expected_version` returns `409` conflict.
- `GET /providers/state` includes `governance_audit`.
- Verified rollback flow end-to-end for provider models contract:
- applied a controlled config update, captured new version, rolled back using expected version, and confirmed exact document+version restoration.
- confirmed audit tail includes sequential `apply` and `rollback` actions.
- Started Phase B modularization slice:
- extracted shared API client into [web/js/api.js](web/js/api.js)
- extracted shared DOM/UI helpers into [web/js/ui-core.js](web/js/ui-core.js)
- switched [web/index.html](web/index.html) dashboard script to module mode and wired [web/app.js](web/app.js) imports.
- Continued Phase B modularization by splitting domain logic into:
- [web/js/domains/operations.js](web/js/domains/operations.js)
- [web/js/domains/router.js](web/js/domains/router.js)
- [web/js/domains/evaluation.js](web/js/domains/evaluation.js)
- [web/js/domains/providers-budget.js](web/js/domains/providers-budget.js)
- Reduced [web/app.js](web/app.js) to orchestration wiring for domain modules.
- Added smoke script [run/webui_smoke.py](run/webui_smoke.py) covering the 4 critical paths:
- switch+bounce, eval suite rerun, OpenRouter catalog refresh, budget warn/exceeded visibility.
- Executed smoke script successfully with `ok: true` across all checks.
- Implemented Phase B information architecture in [web/index.html](web/index.html):
- added top-level section navigation for Operations, Router, Evaluation, Providers/Budget, Jobs
- added read-vs-mutate surface filter controls and section/card surface tagging
- separated Router read snapshot from Router mutation action surface
- Introduced shell wiring in [web/js/domains/shell.js](web/js/domains/shell.js) and converted [web/app.js](web/app.js) back to a thin domain orchestrator.
- Started Phase C runs/filters UX in [web/js/domains/evaluation.js](web/js/domains/evaluation.js) and [web/index.html](web/index.html):
- evaluation runs table with action buttons (Report/Compare/Raw)
- full `/router/evaluations` filter controls (`status`, `target_mode`, `project`, `model`, `provider`, `lane`, `tag`, `suite_pass`, `since_ts`, `limit`)
- URL query-state persistence using `er_*` params for filter state restoration
- Re-ran smoke checks after UI shell + evaluation changes via [run/webui_smoke.py](run/webui_smoke.py), result `ok: true`.
- Added Phase C triage filter presets in [web/index.html](web/index.html) + [web/js/domains/evaluation.js](web/js/domains/evaluation.js):
- `Failures: recent completed`, `Infra: error status`, `Intent regressions`, `OpenRouter fallbacks`, `Queue watch`.
- Implemented run detail drawer workflow using `/router/evaluations/{run_id}` in [web/js/domains/evaluation.js](web/js/domains/evaluation.js):
- row-level and quick-action `Details` open a drawer with run summary, recommendations, and failed/risky case output visibility.
- drawer state is URL-persisted via `er_run_id` and can be deep-linked/restored.
- Implemented compare-compact side-by-side renderer in [web/js/domains/evaluation.js](web/js/domains/evaluation.js) + [web/index.html](web/index.html):
- Compare action now loads `/router/evaluations/{run_id}/compare-compact` into drawer table rows grouped by case/variant.
- Outputs render side-by-side by provider/lane/model with compact output text and inline error signal.
- Re-ran smoke checks after compare renderer changes via [run/webui_smoke.py](run/webui_smoke.py), result `ok: true`.
- Completed Phase C suite workflows in [web/js/domains/evaluation.js](web/js/domains/evaluation.js) + [web/index.html](web/index.html):
- added evaluation suites table with Load/Rerun/Delete actions backed by `/router/evaluation-suites` list/get/delete APIs.
- added suite editor with baseline template generation and Save/Upsert via `PUT /router/evaluation-suites/{suite_name}/{suite_version}`.
- added explicit suite rerun controls with async checkbox and priority selector, wired to `/router/evaluation-suites/{suite_name}/{suite_version}/rerun`.
- surfaced queue and worker context directly above runs table and in run detail metadata context.
- Re-ran smoke checks after suite workflow changes via [run/webui_smoke.py](run/webui_smoke.py), result `ok: true`.
- Restored governance write/rollback contracts in [api/server.py](api/server.py):
- `PUT /providers/models`, `POST /providers/models/rollback`, `PUT /providers/policies`, `POST /providers/policies/rollback`.
- deterministic document version hashing, optimistic concurrency checks, validate-only mode, reason+actor capture, runtime audit trail, and rollback snapshots.
- Added structured request logging middleware in [api/server.py](api/server.py) with `request_id`, `method`, `path`, `status`, and `duration_ms`, and `X-Request-Id` response header.
- Implemented thin governance UI in [web/index.html](web/index.html) + [web/js/domains/governance.js](web/js/domains/governance.js):
- policy/catalog editor with validate/apply actions, rollback with confirmation, version display, and governance audit table.
- Added provider-model flags workflow and audit reference propagation:
- API: `POST /providers/state/provider-model-flags` in [api/server.py](api/server.py).
- UI: provider-model flags controls in [web/index.html](web/index.html) + [web/js/domains/governance.js](web/js/domains/governance.js).
- governance write/rollback responses now include `audit_ref` for explicit post-mutation traceability.
- Improved router triage readability in [web/index.html](web/index.html) + [web/js/domains/router.js](web/js/domains/router.js):
- added top-line health/fallback/queue/budget summary pills so incident status is visible without scanning raw JSON.
- Updated docs in [README.md](README.md), [docs/API.md](docs/API.md), [docs/2bananas/apps/llm-manager/API.md](docs/2bananas/apps/llm-manager/API.md), and [docs/guides/EXTERNAL_INTEGRATION.md](docs/guides/EXTERNAL_INTEGRATION.md).
- Verified governance contracts via [run/governance_smoke.py](run/governance_smoke.py): policies validate + models validate/apply/conflict/rollback + provider-model flags update all passed; audit tail showed `validate`, `apply`, `rollback`.

### 2026-05-07
- Completed final operations-surface cleanup by removing the duplicate Dashboard Model Switcher and preserving engine-card switching as the single canonical switch path in [web/index.html](web/index.html) and [web/js/domains/operations.js](web/js/domains/operations.js).
- Completed final smoke-hardening pass by fixing the OpenRouter refresh smoke stub signature in [run/webui_smoke.py](run/webui_smoke.py).
- Executed and passed final smoke validations:
  - `python run/webui_smoke.py`
  - `python run/tgw_webui_smoke.py`
- Updated closeout status and retirement decision in [docs/plans/WEBUI_OVERHAUL_CLOSEOUT.md](docs/plans/WEBUI_OVERHAUL_CLOSEOUT.md).
