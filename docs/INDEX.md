# LLM Manager Documentation Index

Use this page to determine documentation authority.

## Current operation and contracts

- [README](../README.md) — current architecture, deployment model, and quick start
- [API](API.md) — canonical HTTP contract
- [CLI Operations Guide](CLI_SYSOP_GUIDE.md) — canonical operator runbook
- [Model Hosting](MODEL_HOSTING.md) — supported backend/format matrix
- [External Integration](guides/EXTERNAL_INTEGRATION.md) — consumer onboarding
- [Engine Layout Runbook](guides/RB-ENGINES-LAYOUT.md) — canonical engine/model filesystem layout
- [2026-07-26 Foundation Review](reviews/2026-07-26-foundation-review.md) — latest
  evidence-backed host and beta-readiness review

## Current planning authority

- [Roadmap](plans/ROADMAP.md) — phased priorities
- [Master Checklist](plans/CHECKLIST.md) — actionable backlog
- [Design Decisions](plans/DESIGN_DECISIONS.md) — active architectural decisions
- [Technology Watch](plans/TECH_WATCH.md) — optional future technologies

`plans/NEXT_PUSH.md` is retained as historical delivery notes. It is not the
current backlog.

## Historical evidence

- `reports/` contains dated test, model, and recovery evidence. Reports describe
  the state at their timestamp and are not current service-health authority.
- `plans/retired/` contains completed or superseded plans.
- `TODO/` contains dated workflow notes that have not yet been consolidated or
  retired.

When behavior changes, update the narrowest canonical document first, then the
roadmap/checklist if priority or completion state changed.
