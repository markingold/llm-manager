<!--
id: PLAN-WEBUI-OVERHAUL-CLOSEOUT
version: 1.0
last_updated: 2026-05-07
title: WebUI Overhaul Closeout
purpose:
  Final completion record for the WebUI overhaul, including retirement decision and validation evidence.
-->
# WebUI Overhaul Closeout

Source execution tracker: [docs/plans/retired/WEBUI_OVERHAUL_CHECKLIST.md](docs/plans/retired/WEBUI_OVERHAUL_CHECKLIST.md)
Source implementation plan: [docs/plans/retired/WEBUI_OVERHAUL_PLAN.md](docs/plans/retired/WEBUI_OVERHAUL_PLAN.md)

## Final status
- Overhaul status: completed
- Execution tracker status: retired to historical record
- Plan status: retired to historical record
- Maintenance mode owner: standard roadmap/checklist process

## What was closed in the final pass
1. Removed duplicate operations controls by deleting the Dashboard Model Switcher and keeping engine-card switching as the canonical model switch surface.
2. Verified stable operations controls after cleanup (switch path and TGW control path).
3. Fixed and re-ran smoke harness coverage for OpenRouter refresh path.
4. Brought checklist status, phase status, and retirement notes up to date.

## Validation evidence
- `python run/webui_smoke.py` -> pass
  - switch+bounce path
  - evaluation suite rerun path
  - OpenRouter refresh path
  - budget warning/exceeded visibility path
- `python run/tgw_webui_smoke.py` -> pass
  - TGW WebUI status/config/start/restart/stop path

## Completion gate decision
- Operators can execute core workflows from the dashboard without routine CLI fallback.
- Incident triage path is surfaced directly in Router/Evaluation controls and summary pills.
- Governance edits, audit traceability, and rollback paths are represented in dashboard UX.
- Documentation is updated across project-level and ecosystem-level references.

Result: the WebUI overhaul is complete and can be retired.

## Post-retirement rule
Any future UI work should be tracked in [docs/plans/CHECKLIST.md](docs/plans/CHECKLIST.md) and [docs/plans/ROADMAP.md](docs/plans/ROADMAP.md), not by reopening this overhaul tracker.