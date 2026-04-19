# LLM Manager - Provider Router Plan

> Split from former `docs/engine-ideas.md` on 2026-03-29

## New goal: llm-manager as the universal LLM entry point

## Desired behavior

Any of your projects should be able to send a prompt plus generation arguments to llm-manager and let llm-manager decide the final target.

That means llm-manager should accept requests like:

- use my default general-purpose model
- use a free remote model if possible
- use a paid model if quality matters more
- use local only
- use OpenAI
- use OpenRouter but fail over if the current free model is rate-limited or no longer free

The client project should not need to know whether the final answer came from:

- a local vLLM slot
- a TabbyAPI EXL3 model
- an OpenRouter free model
- an OpenRouter paid model
- an OpenAI model

## What this implies architecturally

llm-manager needs to stop being only a local-engine control API and become a broker API with:

- request normalization
- policy-based routing
- provider adapters
- failure classification
- automatic fallback
- model catalog management
- usage and cost tracking

## Provider routing architecture

## Add provider adapters

Create a provider abstraction with one adapter per external or internal provider:

- `local`
- `openrouter`
- `openai`

Suggested interface shape:

- `list_models()`
- `health()`
- `supports(request_capabilities)`
- `chat(request)`
- `responses(request)` if OpenAI Responses-style support is added later
- `embed(request)`
- `classify(request)` if task-specific routing is added later
- `normalize_error(response_or_exception)`

Suggested modules:

- `api/providers/base.py`
- `api/providers/local.py`
- `api/providers/openrouter.py`
- `api/providers/openai.py`

## Add a routing policy layer

Routing should be policy-driven, not hardcoded per endpoint.

Suggested policy dimensions:

- `preference`: local, remote, cheapest, fastest, best_quality
- `provider_allow`: local, openrouter, openai
- `provider_deny`
- `free_only`: true or false
- `paid_allowed`: true or false
- `local_only`: true or false
- `model_family_preference`: qwen, llama, gpt, claude-like equivalent families if relevant
- `required_capabilities`: tool_calling, structured_output, image_input, embeddings
- `fallback_policy`: strict, permissive, no_fallback

Suggested module:

- `api/router/policy_engine.py`

## Add a unified request contract

llm-manager should expose one normalized request shape for your own projects, regardless of the downstream provider.

Suggested top-level request fields:

- `task_type`: chat, completion, embed, classify
- `messages`
- `system`
- `temperature`
- `top_p`
- `max_tokens`
- `stop`
- `json_schema`
- `tools`
- `provider_preferences`
- `model_preferences`
- `metadata`

Suggested provider preference fields:

- `strategy`: default, local_first, free_first, paid_first, openai_first, openrouter_first
- `free_only`
- `paid_allowed`
- `allow_fallbacks`
- `preferred_provider`
- `preferred_model_tags`

Suggested module:

- `api/router/request_normalizer.py`

## OpenRouter plan

## Maintain a curated model catalog

llm-manager should maintain a local catalog rather than blindly trusting upstream provider discovery for routing decisions.

Suggested catalog split:

- `remote_models.openrouter.free`
- `remote_models.openrouter.paid`
- `remote_models.openai.allowed`

Each model entry should include:

- provider model id
- friendly alias
- enabled flag
- category tags
- capabilities
- cost metadata if known
- priority order
- active flag
- notes

Suggested fields for OpenRouter entries:

- `id`: actual OpenRouter model id
- `label`
- `tier`: free or paid
- `priority`
- `enabled`
- `supports_tools`
- `supports_json_schema`
- `supports_vision`
- `supports_reasoning`
- `max_context`
- `family`
- `notes`

This catalog should be editable without code changes.

Suggested storage:

- JSON or YAML under `config/`
- optionally persisted admin edits via the API later

Example file ideas:

- `config/provider_models.json`
- `config/provider_policies.json`

## Keep a live list of free OpenRouter models

There are two separate needs here:

1. upstream awareness of what OpenRouter currently marks as free
2. your curated subset of free models that you actually want to use

llm-manager should track both.

Recommended behavior:

- periodically fetch the upstream OpenRouter model list
- cache upstream metadata locally
- mark which upstream models appear free
- intersect that with your curated allowlist
- produce an `active_free_candidates` list for routing

This avoids routing to random newly free models that have not been vetted.

## Detect when a free model is no longer usable

llm-manager should normalize OpenRouter failures into a small internal taxonomy, for example:

- `rate_limited`
- `quota_exhausted`
- `not_free_anymore`
- `model_unavailable`
- `provider_error`
- `auth_error`
- `invalid_request`
- `context_too_large`

Examples of signals llm-manager should learn from:

- HTTP status code
- provider error code
- provider error message text
- model-specific disable events repeated over time

When a model returns an error that indicates:

- it is no longer free
- free usage is exhausted
- it is temporarily unavailable

llm-manager should mark it as degraded in a local state table and try the next eligible candidate.

## Add automatic free-model cycling

This should be a routing feature, not something every client has to implement.

It should also respect the operational constraint that free-model traffic needs to stay under roughly 20 requests per minute to avoid unnecessary limiting.

Recommended free-tier selection algorithm:

1. start with the curated and enabled free model list, sorted by priority
2. remove models currently in cooldown
3. remove models missing required capabilities for the request
4. try the highest-priority candidate
5. if the provider returns a retryable or tier-related failure, record the failure and try the next one
6. if all free candidates fail and policy allows paid fallback, move into paid candidates
7. if paid fallback is not allowed, return a clear routing failure

Recommended state kept per provider model:

- last success time
- last failure time
- failure count window
- cooldown until
- last classified error
- disabled_until_manual_review flag for repeated permanent-tier failures

## Add free-tier rate limiting and request queueing

Free-model routing should include a provider-aware rate limiter, not just failure fallback.

Recommended behavior:

- treat OpenRouter free traffic as a separate rate-limited pool
- cap free-model requests at 20 requests per minute by default
- enforce the cap before requests are sent upstream
- queue excess free-tier requests when policy allows waiting
- optionally escalate to paid or local fallback if queue delay would be too high and policy permits it

This matters because rate-limit avoidance is better than repeatedly hitting the provider and then reacting to failures.

Recommended implementation model:

- a token-bucket or leaky-bucket limiter for the OpenRouter free tier
- one shared limiter for all free-tier traffic, plus optional per-model cooldown state
- a small durable request queue for free-tier requests that cannot be dispatched immediately

Suggested queue controls:

- `max_queue_depth`
- `max_queue_wait_ms`
- `queue_behavior`: wait, fail_fast, upgrade_to_paid, fallback_to_local
- `priority`: interactive, batch, evaluation

Recommended routing behavior when the free queue is engaged:

1. if free-tier capacity is available, dispatch immediately
2. if free-tier capacity is exhausted and the request is allowed to wait, enqueue it
3. if the request should not wait and paid fallback is allowed, route to the next paid or local candidate
4. if neither waiting nor fallback is allowed, return a clear rate-limit or queue-capacity response

This should be visible in logs and observability so you can tell the difference between:

- upstream rate limiting
- local protective throttling
- queue overflow

## Distinguish temporary failure from permanent model-tier change

Recommended behavior:

- `rate_limited` or transient `5xx`: short cooldown
- `quota_exhausted`: medium cooldown, maybe until a configurable reset window
- `not_free_anymore`: remove from automatic free rotation and flag for catalog review
- repeated `model_not_found` or `access_denied`: disable until manual review

This is the part where llm-manager learns from the error in a practical operational sense. It does not need ML. It needs persistent classified error state.

## OpenRouter paid-model plan

OpenRouter paid models should be treated as a separate curated tier, not just a fallback after free.

Recommended modes:

- `free_first`
- `paid_first`
- `paid_only`
- `free_only`

Suggested behavior:

- curate which paid models are eligible
- choose among them by priority or capability fit
- jump directly to eligible paid models if a request needs capabilities not present in the free list and policy allows it
- record estimated or actual billed usage where available

## OpenAI plan

OpenAI support should be simpler than OpenRouter support because there is less need for free-tier cycling.

Recommended behavior:

- maintain a curated allowlist of OpenAI model ids
- let routing policies choose OpenAI when explicitly requested or when capability requirements match your preferences
- support a default OpenAI model plus per-task overrides

Suggested OpenAI routing modes:

- explicit `provider = openai`
- `best_quality` strategy if OpenAI should be a premium path
- fallback from OpenRouter paid or local only if explicitly permitted

## Local plus remote routing strategies

Support these strategies first:

### `local_first`

- Try eligible local backends first.
- If no local candidate supports the request and remote fallback is allowed, move to OpenRouter paid or OpenAI based on policy.

### `free_first`

- Try curated OpenRouter free models in priority order.
- Auto-cycle on rate limit, free-tier exhaustion, or a model no longer being free.
- Optionally fall back to local or paid if configured.

### `paid_first`

- Use curated OpenRouter paid or OpenAI models first.
- Fall back according to priority if the preferred provider is unavailable.

### `best_available`

- Use a ranked candidate list across local, OpenRouter paid, OpenRouter free, and OpenAI according to a configurable score.

### `strict_provider`

- Only use the specified provider.
- No cross-provider fallback.

## New data model needed for this plan

Suggested persistent structures:

- `provider_models`
- `provider_model_state`
- `provider_rate_limits`
- `provider_request_queue`
- `routing_policies`
- `request_logs`
- `usage_logs`

### `provider_models`

Static or semi-static curated records.

### `provider_model_state`

Runtime mutable state such as:

- cooldown
- last error
- health
- last free-tier confirmation
- availability score

### `provider_rate_limits`

Runtime state for shared caps such as:

- free-tier requests per minute
- current token balance
- refill timing
- queue pressure

### `provider_request_queue`

Queued requests that are intentionally waiting for free-tier capacity or scheduled evaluation runs.

Suggested fields:

- request id
- project id or caller id
- request class
- enqueue time
- deadline
- routing strategy
- fallback permissions
- current queue state

### `routing_policies`

Per project, per task type, or global defaults.

### `request_logs`

Useful for debugging routing choices and failures.

### `usage_logs`

Needed for later reporting for spend, token usage, local versus remote usage mix, and failure rates by provider.

## API surface to add

## Unified inference endpoints

Keep the current engine-management APIs, but add a new broker-facing layer for projects.

Suggested endpoints:

- `POST /router/chat`
- `POST /router/completions`
- `POST /router/embed`
- `POST /router/route-test`

These should:

- accept normalized requests
- choose provider and target model
- forward to the downstream API
- return normalized responses plus optional routing metadata

## Catalog and policy endpoints

Suggested admin endpoints:

- `GET /providers/models`
- `GET /providers/state`
- `POST /providers/openrouter/refresh`
- `GET /providers/openrouter/free-candidates`
- `GET /providers/openrouter/rate-limit-state`
- `POST /providers/policies/test`
- `GET /providers/policies`
- `POST /providers/policies`

## Observability endpoints

Suggested endpoints:

- `GET /router/health`
- `GET /router/last-decisions`
- `GET /router/fallback-stats`
- `GET /router/usage-summary`
- `GET /router/queue-state`

## Routing decision logging

Every routed request should be able to explain:

- requested strategy
- candidate list considered
- chosen provider
- chosen model
- fallback chain if any
- final outcome

This is essential because once llm-manager brokers across local and remote providers, silent magic becomes hard to debug.

## Security and secrets implications

To support OpenRouter and OpenAI cleanly, llm-manager will need provider credentials in its own secret configuration.

Suggested secrets:

- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`

Suggested supporting config:

- `OPENROUTER_API_BASE`
- `OPENAI_API_BASE`
- `DEFAULT_ROUTING_STRATEGY`
- `DEFAULT_FREE_FALLBACK_ALLOWED`
- `DEFAULT_PAID_FALLBACK_ALLOWED`

Projects that call llm-manager should not need direct provider API keys if llm-manager is acting as the broker.

## Detailed implementation phases for the provider-router plan

## Implementation progress snapshot

As of 2026-03-29:
- curated provider model catalog config added at `config/provider_models.json`
- routing and default policy config added at `config/provider_policies.json`
- provider API base and routing defaults added to `config/settings.example.env`
- provider config read endpoints added: `GET /providers/models`, `GET /providers/policies`
- provider runtime-state scaffold added at `run/state/provider_runtime_state.json` with read endpoint `GET /providers/state`
- normalized chat broker request and response contracts added at `api/router/contracts.py`
- provider adapter modules added at `api/providers/base.py`, `api/providers/local.py`, `api/providers/openrouter.py`, `api/providers/openai.py`
- unified broker endpoint added: `POST /router/chat` with adapter-backed dispatch and policy-chain fallback
- additional broker endpoints added: `POST /router/completions` and `POST /router/embed`
- initial router observability endpoints added: `GET /router/health`, `GET /router/last-decisions`, `GET /router/usage-summary`
- OpenRouter free-tier local limiter implemented via provider_rate_limits state and policy `free_rate_limit_rpm`
- provider-model cooldown state tracking implemented in provider_model_state for retryable and rate-limit failures
- free-tier overflow queue behavior implemented for `wait`, `fail_fast`, `fallback_to_local`, and `upgrade_to_paid`
- router observability endpoints expanded with `GET /router/queue-state` and `GET /router/fallback-stats`
- local evaluation pipeline endpoints added: `POST /router/evaluate/local`, `GET /router/evaluations/{run_id}`, `GET /router/evaluations/{run_id}/report`
- evaluation observability endpoint added: `GET /router/evaluation-summary`
- provider runtime state now persists `evaluation_suites`, `evaluation_runs`, and `evaluation_reports`
- async local evaluation queue endpoint added: `POST /router/evaluate/local/async` with priority classes `interactive`, `batch`, `evaluation`
- evaluation queue observability and control endpoints added: `GET /router/evaluation-queue-state`, `POST /router/evaluation-queue/{run_id}/cancel`
- evaluation suite management and rerun endpoints added: `GET/PUT/DELETE /router/evaluation-suites/{suite_name}/{suite_version}`, `POST /router/evaluation-suites/{suite_name}/{suite_version}/rerun`
- compact compare artifact endpoint added: `GET /router/evaluations/{run_id}/compare-compact`
- evaluation worker configuration endpoint added: `GET /router/evaluation-worker-config`
- dashboard Evaluation Ops panel added in `web/` for queue and report visibility

## Phase 0: shape the config and data model

1. Add provider config files for curated OpenRouter free models, OpenRouter paid models, and OpenAI models.
2. Add secret config for provider API keys.
3. Add runtime state storage for provider model health and cooldowns.
4. Add persistent state for provider rate limits, queues, and evaluation artifacts.

## Phase 1: unified request and provider adapters

1. Add request normalization and response normalization.
2. Implement provider adapters for local, OpenRouter, and OpenAI.
3. Add unified `/router/chat` endpoint.
4. Add the shared routing and queueing primitives needed for protective throttling.

## Phase 2: curated routing and manual selection

1. Route by explicit provider and explicit model.
2. Support curated free and paid lists.
3. Add policy-based model selection without fallback learning yet.
4. Add a free-tier rate limiter capped at 20 requests per minute with optional queueing.

## Phase 3: automatic OpenRouter free-model cycling

1. Fetch and cache upstream OpenRouter model metadata.
2. Intersect upstream free-model status with the curated free list.
3. Add error normalization for OpenRouter failures.
4. Add cooldown and failover state.
5. Automatically cycle to the next free model when the current one becomes unusable.
6. Distinguish between local protective throttling and upstream provider limiting in logs and reports.

## Phase 4: cross-provider fallback

1. Add policy options such as `free_first`, `local_first`, and `best_available`.
2. Permit controlled fallback from free OpenRouter to paid OpenRouter, local, or OpenAI.
3. Add request decision logs and admin inspection endpoints.

## Phase 5: cost, quota, and governance features

1. Record token usage by provider and model.
2. Track approximate spend for paid providers.
3. Add per-project policy overrides if different projects should prefer different providers.
4. Add optional budget guardrails for paid routing.
5. Add model-lane sufficiency reporting so you can tell when a cheaper tier is already good enough for a task.

## Summary recommendation

Turn llm-manager into a provider-and-backend orchestrator, with local backends managed through vLLM, TabbyAPI, and TGW as appropriate, and with OpenRouter free, OpenRouter paid, and OpenAI exposed through the same broker API for all projects.
