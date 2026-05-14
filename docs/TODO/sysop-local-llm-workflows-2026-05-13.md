# TODO: Evaluate SysOp Local-LLM Helper Ideas For llm-manager

## Status: IN-PROGRESS

## Why This Exists

Most of SysOp's original "make my own Copilot" ambition is obsolete now that GitHub Copilot is the normal interface.

Some future ideas are still worth preserving, though, especially where a local or cheap model can preprocess noisy data without spending Copilot or hosted API tokens.

This TODO captures the subset of SysOp future ideas that may belong in `llm-manager`.

## Source Material

Primary SysOp sources:
- `/srv/2bananas/projects/sysop/docs/IDEAS.json`
- `/srv/2bananas/projects/sysop/docs/Project Future Plans.md`
- `/srv/2bananas/projects/sysop/docs/SYSOP_DECOMMISSION_PLAN.md`

## Candidate Features Worth Preserving

### 1. Local log distillation before expensive reasoning

Preserve the idea of:
- sending raw logs to a local model first
- clustering or filtering noisy lines
- returning a bounded distilled summary plus pointers to raw evidence
- optionally passing only the distilled output to a more capable model or to Copilot

This is one of the clearest cases where custom software may still be useful.

### 2. Cheap-model classification and triage

Potential use cases:
- classify error types
- bucket logs by likely component
- identify likely duplicate incidents
- compress large diagnostic inputs into a smaller evidence pack

### 3. Context-budget helper policies

Preserve ideas around:
- minimal vs standard vs maximal context bundles
- provider-aware evidence sizing
- reducing token waste by doing cheap local preprocessing first

### 4. Session-primer or evidence-summary helpers

If `llm-manager` ends up serving other local-model workflows, it may be useful to support:
- short project/evidence briefing generation
- summary output optimized for handoff into Copilot or a larger hosted model

## What Probably Does Not Belong Here

Do not carry over:
- generic repo planning prompts
- SysOp run orchestration
- approval-gated patch application
- project inventory features that belong in `code-indexer`

## Concrete Implementation Backlog

### Proposed target files

- Diagnostics or helper workflow doc: `/srv/2bananas/projects/llm-manager/docs/LLM_TOOLING.md`
- API or workflow contract doc: `/srv/2bananas/projects/llm-manager/docs/API.md`
- Service planning note if needed: `/srv/2bananas/projects/llm-manager/docs/services-plans.md`
- Runtime implementation candidates: `/srv/2bananas/projects/llm-manager/` service or helper modules once the feature is accepted

### Suggested slice order

1. Write the log-distillation contract and expected input/output behavior.
2. Decide whether the feature is local-only, optional, and non-default.
3. Prototype the smallest useful local log summarization path.
4. Only then decide whether any API or service surface is justified.

## Priority Checklist

### High priority
- [ ] Decide whether local log distillation is a real `llm-manager` feature candidate.
- [ ] Define a safe input/output contract for local log summarization.
- [ ] Decide whether raw logs stay local-only and whether any redaction step is required.

### Medium priority
- [ ] Explore cheap-model classification helpers for diagnostics.
- [ ] Explore context-budget policy helpers that can feed Copilot or other consumers.

### Lower priority
- [ ] Evaluate whether `llm-manager` should expose a reusable summary API for large evidence inputs.

## Suggested First Slice

1. Prototype a local-only log distillation workflow.
2. Measure whether it actually improves troubleshooting signal and reduces token-heavy follow-up.
3. If it works, formalize it as an optional helper rather than a required path.

## Done Means

This TODO is done when the still-useful local-LLM ideas from SysOp have either been accepted as real `llm-manager` candidates or explicitly rejected so they do not disappear by accident.
