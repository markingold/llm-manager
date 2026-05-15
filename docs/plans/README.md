# LLM Manager - Plans Folder Guide

## Consolidated planning documents

These are the top-level documents to use when deciding what to build next:

- `ROADMAP.md` - phased prioritization across the whole project
- `CHECKLIST.md` - flat actionable task list with effort sizing
- `DESIGN_DECISIONS.md` - active architectural decisions, conflicts, and open questions
- `TECH_WATCH.md` - future technology watchlist and prerequisites

## Companion source plans

These are the maintainable source plans that replace the old single-file engine plan:

- `BACKEND_ARCHITECTURE_PLAN.md` - local backend architecture, engine inventory, and backend integration strategy
- `EXLLAMA_CONVERSION_PLAN.md` - EXL2 and EXL3 research, conversion workflows, and ExLlama-lane decisions
- `PROVIDER_ROUTER_PLAN.md` - provider abstraction, routing policy, OpenRouter and OpenAI integration, rate limiting, queueing, and governance

## Historical (retired)

- `retired/WEBUI_OVERHAUL_PLAN.md` - retired historical planning artifact for the completed WebUI overhaul
- `retired/WEBUI_OVERHAUL_CHECKLIST.md` - retired execution log for the completed WebUI overhaul
- `retired/WEBUI_OVERHAUL_CLOSEOUT.md` - final closeout decision and validation evidence
- `retired/OPENROUTER_FREE_MODEL_DISCOVERY_PLAN.md` - retired source plan; implemented scope folded into router and observability plans, with follow-on gaps tracked in consolidated docs
- `retired/EVALUATION_PLAN.md` - retired source plan; implemented scope folded into roadmap and checklist tracking

## Maintenance rule

When planning changes are made:

1. update the most specific active companion source plan first
2. then update the consolidated documents if prioritization, checklist items, or decisions changed
3. when a source plan is closed, move it to `docs/plans/retired/` and update consolidated references to point at the retired path
