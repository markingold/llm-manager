# LLM Manager - OpenRouter Free Model Discovery Plan

> Retired to `docs/plans/retired/` on 2026-05-15 after implementation closeout.
> Active tracking now lives in `docs/plans/CHECKLIST.md`, `docs/plans/ROADMAP.md`, and `docs/plans/NEXT_PUSH.md`.

## Goal

Maintain a reliable fallback path for OpenRouter free models when the currently curated free models:

- disappear
- stop being free
- become degraded
- get replaced by newer free models

The result should be a discovery pipeline that finds candidate free models, filters them by practical constraints such as size, popularity, context window, and capabilities, and then promotes only vetted models into the active free rotation.

## Important design choice

This should not start as brittle HTML scraping.

OpenRouter already exposes a public Models API at `/api/v1/models` with structured metadata including:

- model id
- name
- created timestamp
- context length
- pricing
- supported parameters
- modality information
- expiration date

That API should be the primary source of truth.

HTML scraping should be a secondary enrichment step used only for signals the API does not expose cleanly, especially:

- popularity signals from the public models UI
- ranking/category placement from the public site

## Why this matters in this repo

The repo already has the beginnings of this flow:

- `api/server.py` refreshes the OpenRouter model catalog and caches free ids
- `api/server.py` can enforce upstream free status during lane selection
- `config/provider_models.json` already keeps curated OpenRouter free and paid lists
- `config/provider_policies.json` already contains OpenRouter-specific controls

So the plan should extend the existing catalog path, not create a second model-discovery system.

## Existing baseline

Current behavior is roughly:

1. fetch `/models`
2. treat a model as free when prompt and completion prices are both zero, with `:free` as a fallback signal
3. cache a flat list of free ids
4. intersect curated `openrouter.free` entries with upstream free ids
5. route to the first enabled model by priority

That is a good start, but it is too shallow for automatic replacement because it does not yet capture:

- popularity
- inferred size
- richer capability tags
- health history
- promotion and quarantine states
- replacement recommendations when the curated set is exhausted

## Discovery objectives

The discovery system should answer four questions:

1. Which OpenRouter models are free right now?
2. Which of those are plausible matches for your intended fallback class?
3. Which of those have enough popularity or quality signals to be worth trying?
4. Which of those have actually passed lightweight health checks in this environment?

## Source hierarchy

Use sources in this order.

### 1. Primary source: OpenRouter Models API

Request:

`GET https://openrouter.ai/api/v1/models`

Useful fields already available:

- `id`
- `canonical_slug`
- `name`
- `created`
- `description`
- `context_length`
- `architecture.input_modalities`
- `architecture.output_modalities`
- `supported_parameters`
- `pricing`
- `top_provider.context_length`
- `top_provider.max_completion_tokens`
- `expiration_date`

Use API query parameters where possible to reduce post-processing work:

- `output_modalities=text`
- `supported_parameters=tools`
- `supported_parameters=structured_outputs`

### 2. Secondary source: OpenRouter models UI

Use `https://openrouter.ai/models` only for enrichment.

Observed useful UI-only signals:

- token/popularity counts shown on model cards
- sort modes such as `Most Popular`, `Top Weekly`, `Newest`
- category rankings shown on some cards

These signals are useful for fallback ranking when several free models look similar in the API.

### 3. Optional secondary source: OpenRouter rankings pages

If rankings are exposed in stable HTML or JSON on `openrouter.ai/rankings`, use them as another popularity or quality signal.

This should remain optional because rankings data is more likely to change format than the Models API.

## Proposed normalized catalog shape

Extend the cached OpenRouter catalog into an enriched local record per model.

Suggested fields:

- `id`
- `canonical_slug`
- `name`
- `is_free`
- `free_detection_source`
- `created_ts`
- `expiration_date`
- `context_length`
- `max_completion_tokens`
- `supports_tools`
- `supports_structured_outputs`
- `supports_reasoning`
- `supports_vision`
- `supports_text`
- `family`
- `size_label`
- `estimated_total_params_b`
- `estimated_active_params_b`
- `size_confidence`
- `popularity_tokens`
- `popularity_rank`
- `top_weekly_rank`
- `category_ranks`
- `health_status`
- `last_checked_ts`
- `last_success_ts`
- `failure_count_24h`
- `exclude_from_rotation`
- `promotion_state`
- `notes`

## Free detection rules

Mark a model as free when any of the following are true:

1. `pricing.prompt == 0` and `pricing.completion == 0`
2. model id ends with `:free`
3. model name contains `(free)`

Prefer rule 1 over the others.

Store the detection reason so future debugging is easier.

## Size filtering plan

The Models API does not appear to expose a dedicated parameter-count field, so size must be inferred.

### Size extraction strategy

Infer size from, in order:

1. model id
2. display name
3. description text

Patterns to parse:

- `7b`
- `8b`
- `27b`
- `70b`
- `120b`
- `671b`
- `a3b`, `a12b`, `a22b` style active-parameter tags

Examples:

- `qwen/qwen3-next-80b-a3b-instruct:free` -> total about 80B, active about 3B
- `nvidia/nemotron-3-super-120b-a12b:free` -> total about 120B, active about 12B
- `google/gemma-3-27b-it:free` -> total about 27B

### Size filter behavior

Support filters such as:

- `max_total_params_b`
- `min_total_params_b`
- `max_active_params_b`
- `family_allow`
- `family_deny`
- `max_context`
- `min_context`

If size cannot be inferred confidently, keep the model eligible only when `allow_unknown_size` is true.

## Popularity filtering plan

Popularity should be a weighted enrichment signal, not a hard dependency.

### Suggested popularity signals

- total tokens shown on the OpenRouter models page
- position under `Most Popular`
- position under `Top Weekly`
- category ranks when visible

### Suggested popularity fields

- `popularity_tokens`
- `popularity_sort_rank`
- `top_weekly_rank`
- `category_ranks`
- `popularity_last_scraped_ts`

### Filtering examples

- only consider models with `popularity_tokens >= threshold`
- prefer models in the top `N` popular free models
- prefer models with category ranks for coding or general use

If popularity scraping fails, the discovery flow should continue using API-only data.

## Capability filtering plan

Before considering a free model as an automatic replacement, filter it against required capabilities.

Suggested filterable capabilities:

- tool calling
- structured outputs
- reasoning support
- vision support
- text output support
- minimum context length

This should align with the repo's current model metadata shape in `config/provider_models.json`.

## Promotion model

Discovery and routing should remain separate.

### Discovery pool

All upstream free models that pass basic parsing and filtering.

### Candidate pool

Models from the discovery pool that pass local selection rules, such as:

- free upstream
- not expired
- right modalities
- acceptable size
- acceptable popularity
- acceptable capability set

### Active curated pool

Only models that pass smoke tests should enter active rotation.

Suggested promotion states:

- `discovered`
- `candidate`
- `smoke_passed`
- `active`
- `quarantined`
- `retired`

## Smoke-check phase

When the curated free pool becomes empty or degraded, the system should test a short list of discovered candidates.

Use lightweight checks only:

1. simple chat completion
2. optional JSON-format check if structured outputs are required
3. optional tool-call capability check if tool use is required
4. latency and error classification capture

Do not immediately add every newly free model into production rotation.

## Replacement workflow

When all current curated free models fail:

1. refresh the upstream OpenRouter catalog
2. rebuild the discovery pool from currently free models
3. rank candidates using filters and scoring
4. run smoke checks against the top candidates
5. promote the first passing candidates into a temporary active-free list
6. write those candidates into runtime state first
7. optionally write them back into `config/provider_models.json` after confirmation or after repeated success

This separates emergency recovery from permanent catalog edits.

## Scoring proposal

Use a simple weighted score for initial ranking.

Suggested components:

- free status: required gate
- capability match: high weight
- popularity: medium weight
- size fit: medium weight
- context length: medium weight
- recency: low weight
- prior local success rate: high weight once history exists

Example conceptual score:

`score = capability + popularity + size_fit + context_fit + health_bonus - failure_penalty`

This should stay simple enough to debug from logs.

## Integration points in this repo

Prefer these concrete changes later:

### `api/server.py`

Extend `_refresh_openrouter_catalog()` to:

- persist more fields from the Models API
- infer size metadata
- optionally enrich with scraped popularity signals
- store a richer `openrouter_catalog_cache`

Extend lane selection so `openrouter.free` can:

- prefer curated active models first
- fall back to smoke-passed discovered models if the curated list is exhausted
- skip quarantined models automatically

### `config/provider_models.json`

Keep this as the durable curated catalog.

Add optional fields such as:

- `promotion_state`
- `estimated_total_params_b`
- `estimated_active_params_b`
- `popularity_rank`
- `last_verified_ts`

### `config/provider_policies.json`

Add discovery settings under the existing OpenRouter policy area, for example:

- `auto_discover_free_models`
- `auto_promote_smoke_passed_candidates`
- `require_popularity_signal`
- `min_context_length`
- `allow_unknown_size`
- `max_total_params_b`
- `preferred_families`
- `max_discovery_candidates`

## Operational safeguards

The system should avoid routing surprises.

### Guardrails

- never replace the curated free list blindly with every upstream free model
- never trust HTML-only data over API pricing data
- quarantine models after repeated classified failures
- keep a denylist for models that are technically free but unsuitable
- record why a model was promoted or quarantined

### Logging

For every discovery cycle, log:

- how many models were fetched
- how many were detected as free
- how many passed filters
- how many passed smoke tests
- which models were promoted
- which models were quarantined and why

## Suggested phases

### Phase 1

Enrich the existing catalog refresh with more API metadata and size inference.

### Phase 2

Add popularity enrichment from the public models page as a best-effort scraper.

### Phase 3

Add smoke-tested emergency replacement when curated free models are exhausted.

### Phase 4

Add admin visibility for discovered, promoted, quarantined, and retired models.

## Recommendation

Use the OpenRouter Models API as the primary discovery mechanism and treat page scraping as an enrichment layer for popularity and rankings only.

That gives you a system that is:

- more stable than HTML scraping alone
- capable of filtering by practical size proxies
- capable of preferring popular free models
- aligned with the existing provider router already present in this repo

## Implementation status (retirement closeout 2026-05-15)

Estimated completion for planned scope: 100%.

Status note: capability-specific smoke probes (structured output and tool-calling checks) are now implemented, and the remaining operational cadence concerns are consolidated into ongoing router/backlog docs rather than this retired source plan.

### Completed

- OpenRouter Models API catalog refresh and cache persistence are implemented.
- Free detection rules are implemented (`pricing == 0`, `:free`, `(free)`).
- Catalog records are enriched with capability/modality/context/pricing/expiration data.
- Size inference is implemented from id/name/description (including `A#B` active-parameter tags).
- Popularity enrichment is implemented using rankings scraping and merged into catalog rows.
- Discovery filtering and ranking are implemented (capabilities, size, context, family, popularity).
- Manual activation of top discovered candidates is implemented via `active_ids`.
- Runtime model health state and quarantine/manual-review flags are implemented and used in routing.
- Automatic smoke-check and promotion path is implemented for top discovered candidates (`auto_smoke_check`, `smoke_top_n`, `auto_promote_top_n`).
- Explicit promotion lifecycle state fields and transitions are implemented (`discovered`, `candidate`, `smoke_passed`, `active`, `quarantined`, `retired`).
- Rolling failure-window metrics are implemented and persisted (`failure_count_24h`, `failure_count_7d`) and are now used in scoring and quarantine or retirement decisions.
- Automatic retirement is implemented for expiry and upstream no-longer-free transitions.
- Richer ranking persistence is implemented for discovery/catalog rows (`top_weekly_rank`, `category_ranks`) when rankings data is available.
- Candidate payload surfacing is implemented for lifecycle and smoke evidence (`recent_promotion_transitions`, `recent_smoke_checks`, `lifecycle_evidence`).

### Partially completed

- Discovery filters cover most important controls, but not every optional field proposed in this plan (for example `min_total_params_b`, `max_context`).
- Popularity enrichment still depends on best-effort rankings scraping quality and source stability.

### Not completed

- None blocking for this retired plan's intended scope.

## Immediate next implementation targets (updated)

1. Continue operational hardening through consolidated backlog docs (`NEXT_PUSH`, `CHECKLIST`, `ROADMAP`) instead of re-opening this retired source plan.
