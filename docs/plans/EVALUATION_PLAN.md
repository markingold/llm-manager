# LLM Manager - Evaluation And Model Selection Plan

> Split from former `docs/engine-ideas.md` on 2026-03-29

## Evaluation and model-selection pipeline

## Goal

llm-manager should have a built-in pipeline for testing chat prompts, system messages, task prompts, and other LLM workloads across multiple candidate models, both local and remote, and produce a report you can compare yourself or hand to ChatGPT or Copilot for review.

This should become the mechanism used to answer:

- which model is sufficient for this task?
- can this be handled by a small local 3B to 4B model?
- do you need a medium local 9B to 13B model?
- is an OpenRouter free model good enough?
- which paid model is worth it for this task?
- is GPT worth using here or is a cheaper option sufficient?

## Core requirements

The evaluation system should:

- accept test sets from any of your projects
- run the same test prompts against multiple candidate models
- include both local and remote providers
- preserve raw outputs and relevant metadata
- generate a comparison report suitable for human review or secondary LLM analysis
- allow old test sets to be rerun against newly released models
- support ranking models by cost and sufficiency, not just raw quality

## Suggested model ranking lanes

The report and test runner should understand model lanes in this order:

1. small local models, roughly 3B to 4B
2. medium local models, roughly 9B to 13B
3. OpenRouter free models grouped into a few categories
4. OpenRouter paid models grouped into a few categories
5. OpenAI GPT API models

This ranking should be explicit in the evaluation config so a report can show both:

- absolute behavior by model
- whether a cheaper lane was already sufficient

## Suggested evaluation categories

Structure the curated evaluation matrix around task categories rather than only raw model names.

Examples:

- simple chat
- instruction following
- concise factual answers
- structured JSON output
- tool selection or intent routing
- reasoning-heavy prompts
- long-context prompts
- code generation or code editing if relevant to a given project

Each candidate model can then be tagged with suitability metadata for these categories.

## Evaluation suite format

Each suite should be a portable artifact another project can submit.

Suggested suite fields:

- suite name
- suite version
- project name
- task category
- evaluation notes
- default generation params
- candidate selection policy
- cases

Each test case should include:

- case id
- description
- system message
- messages or prompt
- expected output shape if relevant
- scoring notes
- tags

Optional per-case constraints:

- max tokens
- must return JSON
- tool-calling expected
- latency sensitivity
- cost sensitivity

## Evaluation execution flow

Recommended run flow:

1. receive an evaluation suite from a client project
2. expand the candidate model matrix according to policy and ranking lanes
3. normalize each test case into provider-specific requests
4. run each case against each candidate model
5. capture raw response, timing, token usage, routing info, and errors
6. generate normalized result records
7. produce a comparison report in machine-readable and human-readable forms

The evaluation runner should support both:

- synchronous small runs
- queued batch runs for large suites

## Output artifacts

Each evaluation run should generate at least:

- raw JSON results
- a summarized comparison report
- a compact report optimized for feeding to another LLM for adjudication

Suggested report outputs:

- `results.json`
- `summary.md`
- `llm_compare_input.json` or `llm_compare_input.md`

The compact compare artifact should include enough context for another LLM to judge:

- the task description
- the prompt or system message used
- candidate model names and tiers
- the actual responses
- timing and cost notes

## How other projects should submit tests

There should be a clear documented guide for other projects.

Recommended documentation deliverables later:

- an API guide for sending evaluation suites
- one or two example payloads
- a recommended JSON schema for evaluation suites
- examples for simple chat tests, JSON-output tests, and intent-routing tests

Suggested endpoint design:

- `POST /router/evaluate`
- `POST /router/evaluate/async`
- `GET /router/evaluations/{run_id}`
- `GET /router/evaluations/{run_id}/report`

## Re-testing old suites against new models

This should be a first-class feature, not a manual workaround.

Recommended behavior:

- keep evaluation suites versioned and reusable
- allow an old suite to be rerun against a refreshed candidate list
- allow pinning old baseline models for comparison
- track regressions and improvements over time

This will let you answer questions like:

- can a new free model replace the current paid option?
- can a new 4B local model replace the current 13B local model for this task?
- is a new OpenRouter free model now sufficient for a workload that used to require GPT?

## Reporting dimensions

The evaluation reports should compare more than output text.

Recommended dimensions:

- output quality by human or secondary-LLM judgment
- task sufficiency
- structured-output correctness
- tool-call correctness if applicable
- latency
- token usage
- estimated cost
- provider stability
- whether a cheaper lane already meets the requirement

## Recommended implementation placement

This evaluation pipeline belongs beside the provider router, not inside any one backend adapter.

Suggested modules:

- `api/evaluation/schemas.py`
- `api/evaluation/runner.py`
- `api/evaluation/reporting.py`
- `api/evaluation/storage.py`

## Documentation requirement

The plan should explicitly include documentation for external project integration.

That documentation should eventually cover:

- how to submit a single routed inference request
- how to request `free_first`, `local_first`, or `paid_first`
- how to submit an evaluation suite
- how to retrieve reports
- how to rerun old suites against new model candidates

## Observability endpoints

Suggested endpoints:

- `GET /router/evaluation-summary`
- `GET /router/queue-state`

## New data model needed for evaluation

Suggested persistent structures:

- `evaluation_suites`
- `evaluation_runs`
- `evaluation_results`
- `evaluation_reports`

### `evaluation_suites`

Saved test sets representing prompts, system messages, tasks, and expected comparison context.

### `evaluation_runs`

One execution of an evaluation suite against a chosen model matrix.

### `evaluation_results`

Per-model responses, timings, errors, and normalized metadata.

### `evaluation_reports`

Generated comparison artifacts that you or another LLM can review to decide which model is sufficient for a task.

## Detailed implementation phases for the evaluation track

## Implementation progress snapshot

As of 2026-03-29:
- initial evaluation schema module added at `api/evaluation/schemas.py`
- initial local evaluation run endpoint added: `POST /router/evaluate/local`
- stored run retrieval endpoint added: `GET /router/evaluations/{run_id}`
- compact report retrieval endpoint added: `GET /router/evaluations/{run_id}/report`
- compact compare artifact endpoint added: `GET /router/evaluations/{run_id}/compare-compact`
- evaluation observability endpoint added: `GET /router/evaluation-summary`
- provider runtime state now persists `evaluation_suites`, `evaluation_runs`, and `evaluation_reports`
- initial summary analysis and recommendations added for prompt and temperature tuning across local model candidates
- async local evaluation queue endpoint added: `POST /router/evaluate/local/async` with priority classes
- evaluation queue observability and control endpoints added: `GET /router/evaluation-queue-state`, `POST /router/evaluation-queue/{run_id}/cancel`
- evaluation suite CRUD and rerun endpoints added under `GET/PUT/DELETE /router/evaluation-suites/...` and `POST /router/evaluation-suites/.../rerun`
- evaluation worker config endpoint added: `GET /router/evaluation-worker-config`
- multi-worker and per-priority running caps added for async local evaluation execution
- evaluation case scoring plugin support added for regex/contains/json/length checks to guide prompt and parameter tuning
- threshold-based case and suite pass/fail gating added to local evaluation requests, suites, and reruns
- filtered evaluation run listing endpoint added: `GET /router/evaluations` (status/mode/project/model/tag/since)
- local evaluation summaries now include case pass counts, pass rates, and suite pass status
- dashboard Evaluation Ops now supports rerunning saved suites from the UI
- mixed-provider candidate execution added for local evaluation suites (`local`, `openrouter`, `openai` candidates in one run)
- compare artifacts and run rows now include provider and lane metadata for adjudication and triage
- evaluation filtering expanded with provider, lane, and suite pass fields
- evaluation summaries now include by-provider aggregates and optional estimated cost rollups when catalog pricing metadata is present
- external integration guide added at `docs/guides/EXTERNAL_INTEGRATION.md` for routed inference and suite submission from other projects

## Phase 1: evaluation pipeline foundation

1. Add evaluation suite schemas and storage.
2. Add a runner that can execute a suite across local and remote candidates.
3. Generate raw and summarized comparison reports.
4. Add endpoints for submitting suites and fetching reports.

## Phase 2: evaluation documentation and reuse

1. Write a guide other projects can follow to submit test sets to llm-manager.
2. Provide example evaluation suite payloads.
3. Support rerunning historical suites against newly added models.
4. Add comparison outputs suitable for handing to ChatGPT or Copilot for adjudication.

## Approval-oriented next step for this track

After the core router exists, the next valuable slice is to define the evaluation suite schema, storage model, and report formats before building the runner.
