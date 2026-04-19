# Human-Readable Summary: Latest Baseline + Promotion Reports

Date generated: 2026-03-29T22:31:30.464736Z
Source baseline: docs/reports/baseline_local_models_20260329_223130.md
Source promotion: docs/reports/promotion_recommendation_20260329_223130.md

## Executive Summary

This run shows useful progress in output formatting and basic intent tasks, but arithmetic reliability is still too weak for promotion.

- Total checks run: 112
- Total passed: 44
- Overall pass rate: 39.29%
- Runtime/API errors: 1
- Promotion outcome: all models remain **hold**

Why all models are hold:
- Promotion rules require high pass rate and all hard arithmetic tests to pass.
- No model passed all required arithmetic tests in a lane.

## What Is Working Well

Across models, the strongest categories are:
- Trivia short-answer behavior (`trivia_capital`): 93.75% pass rate
- Sentiment one-token behavior (`sentiment_label`): 81.25% pass rate

Interpretation:
- Most models can follow simple factual prompts and sentiment classification format under this harness.
- Core instruction following is present, but not robust enough under arithmetic pressure.

## Main Failure Area

Arithmetic remains the largest blocker:
- `arithmetic_739x481`: 12.5%
- `arithmetic_913x47`: 25.0%
- `arithmetic_order_ops`: 6.25%
- `arithmetic_order_ops_v2`: 12.5%

Interpretation:
- Failures are not isolated to one arithmetic prompt shape.
- The additional arithmetic variants successfully exposed brittle math behavior across the board.

## Secondary Failure Area

Strict JSON output format is mixed:
- `valid_json_strict`: 43.75% pass rate

Common pattern:
- Models often include extra wrapper text or malformed/irrelevant `answer` values, which fails strict checks.

## Lane-Level Snapshot

### Chat lane

- Pass rate: 37.5% (21/56)
- Errors: 1
- Best model by pass rate: `Qwen3.5-4B-Q6_K` at 57.14%
- All models still hold due to arithmetic gate failures

### Small lane

- Pass rate: 41.07% (23/56)
- Errors: 0
- Best model by pass rate: `Qwen3.5-4B-Q6_K` at 71.43%
- Still hold because hard arithmetic gate is not fully satisfied

## Top Models (Practical Ranking)

If you want a practical “closest to promotion” view (even though formal tier is hold):

1. `Qwen3.5-4B-Q6_K` (best overall in both lanes)
2. `Gemma-3-27B-IT-EXL2-4.0bpw` (stronger in small lane)
3. `Phi-4-mini-instruct` / `meta-llama__Llama-3.2-3B-Instruct` (mid-pack, but arithmetic gaps remain)

## Why Promotion Did Not Happen

Configured gates:
- Promote pass-rate threshold: 0.90
- Conditional threshold: 0.70
- Required hard tests: all four arithmetic tests must pass

Result:
- Some models reach or approach conditional pass-rate in one lane.
- But no model clears the arithmetic must-pass gate set, so formal tier remains hold.

## Suggested Next Iteration

1. Keep current strict gates unchanged (they are doing their job).
2. Add 2-3 more arithmetic prompts with the same strict answer-only rule to stabilize signal.
3. Add a focused arithmetic prompt-template sweep (same math, varied wording) to detect prompt sensitivity.
4. Keep JSON strict test as-is and add one additional strict schema case to confirm non-overfitting.

## Report Files

- Human-readable summary (this file): docs/reports/latest_test_reports_human_readable_20260329_223130.md
- Baseline metrics report: docs/reports/baseline_local_models_20260329_223130.md
- Promotion decision report: docs/reports/promotion_recommendation_20260329_223130.md
