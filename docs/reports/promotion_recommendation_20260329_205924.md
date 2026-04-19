# Promotion Recommendation From Strict Baseline

- Source report: docs/reports/baseline_local_models_20260329_205924.json
- Generated: 2026-03-29T20:59:24.243450Z
- Overall pass rate: 0.45

## Lane Ranking - Chat

1. Qwen3.5-4B-Q6_K | tier=conditional | pass_rate=0.8 | passes=4/5 | errors=0 | json=1 hard1=1 hard2=0 trivia=1 sentiment=1
2. meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5 | tier=hold | pass_rate=0.6 | passes=3/5 | errors=0 | json=0 hard1=0 hard2=1 trivia=1 sentiment=1
3. meta-llama__Llama-3.2-3B-Instruct | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=1
4. Phi-4-mini-instruct | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=0
5. LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=1
6. Gemma-3-27B-IT-EXL2-4.0bpw | tier=hold | pass_rate=0.4 | passes=2/5 | errors=1 | json=0 hard1=0 hard2=0 trivia=1 sentiment=1
7. llama3.1-8B_exl2_b6p5 | tier=hold | pass_rate=0.2 | passes=1/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=0
8. llama3.1-8B | tier=hold | pass_rate=0.0 | passes=0/5 | errors=5 | json=0 hard1=0 hard2=0 trivia=0 sentiment=0

## Lane Ranking - Small

1. Qwen3.5-4B-Q6_K | tier=conditional | pass_rate=0.8 | passes=4/5 | errors=0 | json=1 hard1=1 hard2=0 trivia=1 sentiment=1
2. llama3.1-8B | tier=hold | pass_rate=0.6 | passes=3/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=1
3. Gemma-3-27B-IT-EXL2-4.0bpw | tier=hold | pass_rate=0.6 | passes=3/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=1
4. meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5 | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=1
5. meta-llama__Llama-3.2-3B-Instruct | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=1
6. llama3.1-8B_exl2_b6p5 | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=1
7. Phi-4-mini-instruct | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=0
8. LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=1

## Promotion Summary

- No models meet promote threshold under the tightened strict suite yet.
- Primary blockers remain arithmetic reliability and strict JSON-only conformance under instruction pressure.
- Trivia and sentiment are comparatively stronger in this corrected run, but not sufficient for promotion alone.
