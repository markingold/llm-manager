# Promotion Recommendation From Strict Baseline

- Source report: docs/reports/baseline_local_models_20260329_205223.json
- Generated: 2026-03-29T20:52:23.519386Z
- Overall pass rate: 0.2875

## Lane Ranking - Chat

1. Phi-4-mini-instruct | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=0
2. Qwen3.5-4B-Q6_K | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=0
3. llama3.1-8B | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=0
4. LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 | tier=hold | pass_rate=0.2 | passes=1/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=0
5. llama3.1-8B_exl2_b6p5 | tier=hold | pass_rate=0.2 | passes=1/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=0
6. meta-llama__Llama-3.2-3B-Instruct | tier=hold | pass_rate=0.2 | passes=1/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=0
7. meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5 | tier=hold | pass_rate=0.2 | passes=1/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=0
8. Gemma-3-27B-IT-EXL2-4.0bpw | tier=hold | pass_rate=0.2 | passes=1/5 | errors=1 | json=0 hard1=0 hard2=0 trivia=1 sentiment=0

## Lane Ranking - Small

1. Gemma-3-27B-IT-EXL2-4.0bpw | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=0
2. Phi-4-mini-instruct | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=0
3. Qwen3.5-4B-Q6_K | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=0
4. llama3.1-8B | tier=hold | pass_rate=0.4 | passes=2/5 | errors=0 | json=1 hard1=0 hard2=0 trivia=1 sentiment=0
5. LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 | tier=hold | pass_rate=0.2 | passes=1/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=0
6. llama3.1-8B_exl2_b6p5 | tier=hold | pass_rate=0.2 | passes=1/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=0
7. meta-llama__Llama-3.2-3B-Instruct | tier=hold | pass_rate=0.2 | passes=1/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=0
8. meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5 | tier=hold | pass_rate=0.2 | passes=1/5 | errors=0 | json=0 hard1=0 hard2=0 trivia=1 sentiment=0

## Promotion Summary

- No models meet promote threshold under the tightened strict suite yet.
- Primary blockers are arithmetic strictness and strict JSON-only conformance.
- Trivia remains strong across nearly all candidates, indicating instruction-following for factual short answers is stable.
