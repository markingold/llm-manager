# Production Evaluation

LLM Manager evaluates model and prompt variants with deterministic gates first,
then optional blinded model judging. Runs, queue state, schedules, judge
provenance, spend, and promotion evidence persist in the SQLite runtime store.

## Scoring contract

Each case may define one or more objective checks:

- `expected_contains` (every listed term is required)
- `expected_exact`
- `response_json_schema`
- `expected_tool_name`
- `max_latency_ms`
- `max_estimated_cost_usd`
- weighted scoring plugins such as regex, contains, exact match, JSON validity,
  JSON Schema, and tool-called checks

Objective checks are hard gates. A weighted plugin set without an explicit
threshold defaults to a strict `1.0` threshold. A response with no objective
check can be graded by the optional judge, but it is not treated as passing
merely because the provider returned HTTP success.

Remote evaluation candidates must be enabled in a curated provider catalog.
The only exception is a currently-free OpenRouter model in the persisted,
vetted discovery pool. Free candidates are evaluated through the free lane and
cannot be relabeled as paid (or vice versa).

## Judge stages

When `judge.enabled` is true:

1. `openai/gpt-5.6-luna` grades rubric-based responses that do not already
   have an objective score, using a blinded candidate ID.
2. The top configured fraction advances to `openai/gpt-5.6-terra`.
3. `openai/gpt-5.6-sol` adjudicates only when the first two scores disagree by
   at least the configured threshold.

Each stage has a curated fallback chain. The defaults use DeepSeek V4 Flash,
Gemini 2.5 Flash Lite, and MiniMax M3 behind Luna; Claude Sonnet 5 and Qwen 3.7
Plus behind Terra; and Claude Sonnet 5 behind Sol. Failed attempts and the
actual judge selected are retained with the result.

Candidate output is explicitly presented to judges as untrusted data. Judges
must return JSON matching the stored judgment schema. Each judgment records
model, rubric version, reasoning effort, token usage, latency, estimated cost,
and normalized errors. Deterministic failures remain failures regardless of a
judge score.

`max_estimated_cost_usd` on the evaluation request caps candidate calls.
`judge.max_estimated_cost_usd` independently caps judging. Global daily,
monthly, and per-request provider budgets still apply.

## Automated OpenRouter schedule

The default schedule is stored under `provider_policies.evaluation.scheduler`:

| Job | Default | Purpose |
| --- | --- | --- |
| `daily_health` | every 24 hours | Refresh the upstream catalog, smoke-test active defaults/fallbacks, and immediately advance an available fallback |
| `weekly_discovery` | every 7 days | Refresh catalog/rankings, discover eligible free text models, and send a smoke prompt to each candidate |
| `biweekly_benchmark` | every 14 days | Run two repetitions of the production benchmark across eligible free candidates and apply the staged paid judges |

Jobs use persistent due times and leases so API restarts do not duplicate active
work or reset the schedule. Free calls use the shared priority queue and wait
across provider rate-limit windows; interactive traffic retains higher
priority. Candidate calls have a 45-second wall-clock deadline and judge calls
have a 90-second deadline, so a provider that trickles bytes without completing
cannot monopolize a worker. Both limits are configurable in the evaluation
policy.

Inspect the schedule and current promoted profiles:

```bash
curl -s http://127.0.0.1:8101/router/evaluation-schedule | jq
```

Schedule a job now:

```bash
curl -s -X POST \
  http://127.0.0.1:8101/router/evaluation-schedule/weekly_discovery/run | jq
```

The dashboard Evaluation view exposes the same status and manual run controls.
It warns before scheduling the paid-judge benchmark.

## Promotion rules

The benchmark produces `general`, `reasoning`, `coding`, `structured`, and
`tools` profiles. The composite score is:

- 65% quality (judge score when available, deterministic pass otherwise)
- 20% case pass rate
- 10% provider success rate
- 5% latency score

A model must also meet the configured minimum score, sample count, maximum
failure rate, and 30-second p95 latency ceiling. An incumbent remains primary
unless a challenger exceeds it by `minimum_score_delta`; this avoids churn from
noise. Each profile retains a primary and configured number of fallbacks. Daily
reconciliation removes models that are no longer free or healthy and promotes
the first available fallback.

Promotion changes runtime routing state only. The curated catalog remains an
operator-controlled allowlist and audit boundary.

## Example request

```json
{
  "suite_name": "project-chat-contract",
  "suite_version": "1",
  "target_mode": "chat",
  "candidate_models": [
    "chat_active_model",
    "openrouter.paid:deepseek/deepseek-v4-flash"
  ],
  "variants": [
    {
      "variant_id": "baseline",
      "temperature": 0,
      "max_tokens": 200,
      "seed": 42
    }
  ],
  "cases": [
    {
      "case_id": "json-contract",
      "prompt": "Return the title and year for Dune as JSON.",
      "response_json_schema": {
        "type": "object",
        "properties": {
          "title": {"type": "string"},
          "year": {"type": "integer"}
        },
        "required": ["title", "year"],
        "additionalProperties": false
      },
      "rubric": "Correct title/year and no unsupported fields.",
      "tags": ["structured"]
    }
  ],
  "repetitions": 2,
  "max_estimated_cost_usd": 1,
  "judge": {
    "enabled": true,
    "max_estimated_cost_usd": 1,
    "rubric_version": "project-chat-v1"
  }
}
```

Submit synchronously to `/router/evaluate/local` or asynchronously to
`/router/evaluate/local/async`. Production-sized comparisons should use the
async queue.
