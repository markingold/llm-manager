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
- `OPENROUTER_FREE_MODEL_DISCOVERY_PLAN.md` - OpenRouter free-model discovery, filtering, popularity enrichment, and emergency replacement workflow
- `PROVIDER_ROUTER_PLAN.md` - provider abstraction, routing policy, OpenRouter and OpenAI integration, rate limiting, queueing, and governance
- `EVALUATION_PLAN.md` - multi-model evaluation pipeline, suite format, reporting, and external-project usage
- `WEBUI_OVERHAUL_PLAN.md` - dashboard information architecture, parity gaps, and phased UX overhaul

## Maintenance rule

When planning changes are made:

1. update the most specific companion source plan first
2. then update the consolidated documents if prioritization, checklist items, or decisions changed
3. keep source references in the consolidated docs pointed at the companion source plans, not deleted historical files
