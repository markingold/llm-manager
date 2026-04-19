# LLM Manager - WebUI Overhaul Plan

> Added: 2026-03-29

## Goal

Evolve the current single-page operations dashboard into a scalable control console that covers all major llm-manager capabilities without requiring frequent CLI fallback.

This plan focuses on:
- complete operational coverage of router and evaluation features
- safer controls for budget and fallback policies
- clearer information architecture as the project has grown
- preserving fast day-to-day workflows for switching and health checks

## Current gaps

The current web UI is strong for slot operations and basic evaluation queue visibility, but still lacks complete control-plane coverage:

1. Provider governance controls are read-only.
2. Budget state is visible but budget policy editing and guardrail tuning are missing.
3. OpenRouter metadata refresh is now operator-triggerable, but refresh history, errors, and follow-up guidance are still raw and not workflow-oriented.
4. Evaluation runs are visible in snapshots but lack first-class table views, filtering, and drilldown.
5. Compare artifacts are not rendered in a guided side-by-side review flow.
6. Router decision logs and fallback chains are only surfaced as raw JSON snapshots.
7. There is no cohesive "admin" workflow for model catalog and policy changes.

## Current backend constraints to respect

1. Provider governance endpoints are currently read-only (`GET /providers/models`, `GET /providers/policies`).
2. Safe config mutation contracts are not yet defined for policy/catalog updates.
3. Existing evaluation and router endpoints are strong enough to build most read-only UX without backend rework.

## Design principles

1. Keep primary operational actions one click away:
	switch, restart, solo start, test, rerun suite, refresh provider metadata.
2. Split read surfaces from mutation surfaces:
	monitoring pages should not be cluttered with policy editors.
3. Make routing explainability first-class:
	show lane selection, fallbacks, and budget-block reasons in plain language.
4. Default to safe operations:
	risky changes (provider policy edits, budget changes) require explicit confirmation.
5. Preserve API parity:
	every important endpoint should either have a UI control or an explicit rationale for staying API-only.

## Proposed information architecture

## 1) Operations
- Slot cards (chat, intent, small)
- Active models, backends, health, logs
- Switch and bounce actions

## 2) Router
- Router health summary
- Recent decisions with fallback chain viewer
- Fallback stats and error taxonomy charts
- OpenRouter free-tier queue and cooldown states
- OpenRouter metadata refresh action and refresh status

## 3) Providers & Budget
- Provider catalog summary by tier (local/openrouter free/openrouter paid/openai)
- Spend summary (day, month, lifetime)
- Guardrail status (warn/exceeded)
- Budget policy editor (with validation and confirmation)

## 4) Evaluation
- Suites table with rerun actions
- Runs table with filters (status/mode/project/model/provider/lane/suite pass/tag/since)
- Run drilldown with summary, recommendations, and per-case outputs
- Compare-compact renderer for side-by-side adjudication

## 5) Jobs & Diagnostics
- Train/merge/convert job list and tails
- Engine diagnostics and system snapshots

## Implementation phases

## Phase A - Quick Wins (low risk)
1. Add Router Ops section with budget state, fallback stats, and metadata refresh action.
2. Add one-click OpenRouter metadata refresh button and visible last refresh result.
3. Add direct links/actions for recent evaluation runs from Eval Ops.
4. Add clear warning banner when budget thresholds are near or exceeded.

### Phase A progress (2026-03-29)
- Completed: Router Ops panel with budget/fallback/catalog state snapshot.
- Completed: one-click OpenRouter metadata refresh action in dashboard.
- Started and completed in this slice: recent evaluation run quick actions from Eval Ops (`Report`, `Compare`, `Raw`).
- Started and completed in this slice: budget warning banner in the Status card for warn/exceeded states.

### Concrete UI tasks shipped in this slice
1. Wire `/router/evaluations?status=completed&limit=10` into Eval Ops refresh flow.
2. Render top 5 recent completed runs with one-click actions:
	- `Report` -> `/router/evaluations/{run_id}/report`
	- `Compare` -> `/router/evaluations/{run_id}/compare-compact`
	- `Raw` -> `/router/evaluations/{run_id}`
3. Wire `/router/budget-state` thresholds into a status banner with warn and exceeded tones.
4. Keep banner hidden when below threshold or when router data is unavailable.

## Phase A.5 - Governance API contracts (required before admin UI)
1. Add validated write endpoints for provider policy/catalog mutations (no file-write logic in the browser).
2. Add dry-run validation mode for policy and catalog edits to catch schema/value mistakes before apply.
3. Add optimistic concurrency (`version`/`etag`) to prevent accidental overwrite of concurrent edits.
4. Add append-only audit entries for governance mutations (time, actor, changed keys, reason).
5. Add rollback path for the most recent successful governance change.

## Phase B - Frontend foundation hardening
1. Split `web/app.js` into domain modules (`operations`, `router`, `evaluation`, `providers-budget`, shared `api`/`state`).
2. Introduce route-level sections so read-only dashboards and mutation workflows are clearly separated.
3. Add smoke tests for critical workflows (switch+bounce, eval rerun, router refresh, budget warning visibility).
4. Preserve current UX behavior while modularizing (no large visual redesign in this phase).

## Phase C - Evaluation UX
1. Build a dedicated evaluation runs table with all existing API filters.
2. Add per-run detail drawer and compare artifact viewer.
3. Add simple "baseline suite" templates from UI for common smoke tests.
4. Add query-state persistence (URL params) so operators can share and restore filtered views.

## Phase D - Provider Governance UX
1. Add read-write forms for policy controls in config/provider_policies.json.
2. Add curated catalog editor views for provider model entries.
3. Add provider-model flags management workflow (manual review reset, re-enable rotation).
4. Require confirmation + reason text for high-impact budget/routing edits.
5. Surface audit trail and one-click rollback in the same governance workspace.

## Phase E - Full UI refresh
1. Consolidate styles/components for consistency across all sections.
2. Improve visual hierarchy for at-a-glance operations and faster incident response.
3. Refresh operator docs and screenshots once behavior stabilizes.

## Safety requirements for admin mutations

1. No direct file writes from browser clients.
2. Server-side schema validation before apply.
3. Dry-run validation available from UI and API.
4. Concurrency protection required (`version`/`etag`).
5. Human confirmation required for budget/routing policy changes.
6. Audit log required for every successful mutation.
7. Rollback path must be tested before enabling general operator access.

## API parity checklist for overhaul

- [ ] /providers/openrouter/refresh
- [ ] /providers/models (read)
- [ ] /providers/policies (read)
- [ ] /providers/models (write contract)
- [ ] /providers/policies (write contract)
- [ ] /router/budget-state
- [ ] /router/last-decisions
- [ ] /router/fallback-stats
- [ ] /router/usage-summary
- [ ] /router/queue-state
- [ ] /router/evaluation-queue-state
- [ ] /router/evaluation-summary
- [ ] /router/evaluation-worker-config
- [ ] /router/evaluations with full filter support
- [ ] /router/evaluations/{run_id}
- [ ] /router/evaluations/{run_id}/report
- [ ] /router/evaluations/{run_id}/compare-compact
- [ ] /router/evaluation-suites CRUD and rerun

## Definition of done for overhaul

1. Operators can complete all common workflows without leaving the dashboard.
2. Router decisions and failures are interpretable without reading raw API logs.
3. Budget guardrail state and spend are visible and actionable.
4. Evaluation baseline creation, execution, filtering, and comparison are first-class.
5. Documentation and operator guide screenshots are updated to the new UI.
6. Governance edits are safe-by-default (validation, confirmation, audit, rollback).
7. At least 90% of daily operator tasks can be completed without CLI fallback.
8. Fallback incident triage can reach root cause in under 2 minutes using Router views.
