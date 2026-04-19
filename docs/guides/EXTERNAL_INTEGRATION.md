<!--
id: LLM-MANAGER-EXTERNAL-INTEGRATION
version: 1.0
last_updated: 2026-03-29
title: External Integration Guide
purpose:
  Show other projects how to call routed inference and submit evaluation suites to llm-manager.
-->

# External Integration Guide

## Base URL

- Local direct: `http://127.0.0.1:8101`
- Typical Apache path: `/llm-manager-api`

## 1) Routed inference from another project

### Chat

```bash
curl -X POST http://localhost:8101/router/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "task_type": "chat",
    "messages": [{"role":"user","content":"Give one-line weather summary"}],
    "provider_preferences": {"strategy":"local_first"},
    "metadata": {"project":"example-client","use_case":"weather"}
  }' | python3 -m json.tool
```

### Completions

```bash
curl -X POST http://localhost:8101/router/completions \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Say OK","max_tokens":8}' | python3 -m json.tool
```

### Embeddings

```bash
curl -X POST http://localhost:8101/router/embed \
  -H 'Content-Type: application/json' \
  -d '{"input":"hello world"}' | python3 -m json.tool
```

## 2) Submit evaluation suites

### Sync run (small batch)

```bash
curl -X POST http://localhost:8101/router/evaluate/local \
  -H 'Content-Type: application/json' \
  -d '{
    "suite_name":"external-chat-smoke",
    "suite_version":"1",
    "target_mode":"chat",
    "candidate_models":[
      "chat_active_model",
      "openrouter:meta-llama/llama-3.3-8b-instruct:free",
      "openai:gpt-4o-mini"
    ],
    "case_pass_threshold_pct":0.8,
    "suite_pass_threshold_pct":0.9,
    "variants":[{"variant_id":"baseline","temperature":0.2,"max_tokens":120}],
    "cases":[
      {
        "case_id":"wx1",
        "prompt":"Give a one-line weather summary",
        "expected_contains":["weather"],
        "tags":["weather","summary"]
      }
    ],
    "metadata":{"project":"example-client","owner":"qa"}
  }' | python3 -m json.tool
```

Candidate model syntax accepted today:
- local model alias/name: `chat_active_model`, `intent_active_model`, `small_active_model`, or a local model directory
- explicit local mode target: `local:chat:chat_active_model`
- OpenRouter model: `openrouter:<model_id>`
- OpenAI model: `openai:<model_id>`

### Async run (large batch)

```bash
curl -X POST "http://localhost:8101/router/evaluate/local/async?priority=batch" \
  -H 'Content-Type: application/json' \
  -d '{
    "suite_name":"external-chat-regression",
    "suite_version":"1",
    "target_mode":"chat",
    "candidate_models":["chat_active_model"],
    "variants":[{"variant_id":"baseline","temperature":0.2,"max_tokens":120}],
    "cases":[{"case_id":"c1","prompt":"Say weather","expected_contains":["weather"]}],
    "metadata":{"project":"example-client"}
  }' | python3 -m json.tool
```

## 3) Inspect runs, reports, and queue

```bash
# list runs (provider/lane/suite pass filters supported)
curl "http://localhost:8101/router/evaluations?status=completed&project=example-client&provider=openrouter&lane=openrouter_free&suite_pass=true&limit=20" | python3 -m json.tool

# fetch one run
curl "http://localhost:8101/router/evaluations/<run_id>" | python3 -m json.tool

# fetch compact report
curl "http://localhost:8101/router/evaluations/<run_id>/report" | python3 -m json.tool

# fetch compact compare artifact for adjudication
curl "http://localhost:8101/router/evaluations/<run_id>/compare-compact" | python3 -m json.tool

# queue state
curl "http://localhost:8101/router/evaluation-queue-state?limit=50" | python3 -m json.tool
```

## 4) Save suites and rerun later

```bash
# upsert suite
curl -X PUT "http://localhost:8101/router/evaluation-suites/external-chat-smoke/1" \
  -H 'Content-Type: application/json' \
  -d '{
    "suite_name":"external-chat-smoke",
    "suite_version":"1",
    "target_mode":"chat",
    "candidate_models":["chat_active_model"],
    "variants":[{"variant_id":"baseline","temperature":0.2,"max_tokens":120}],
    "cases":[{"case_id":"wx1","prompt":"Give a one-line weather summary","expected_contains":["weather"]}],
    "metadata":{"project":"example-client"}
  }' | python3 -m json.tool

# rerun async with threshold overrides
curl -X POST "http://localhost:8101/router/evaluation-suites/external-chat-smoke/1/rerun" \
  -H 'Content-Type: application/json' \
  -d '{"async_run":true,"priority":"batch","case_pass_threshold_pct":0.85,"suite_pass_threshold_pct":0.95}' | python3 -m json.tool
```

## 5) Notes for external projects

## 6) Governance writes (safe-by-default)

Provider config writes support validate-only, optimistic concurrency, audit, and rollback.

```bash
# read policies + version
curl "http://localhost:8101/providers/policies" | python3 -m json.tool

# validate-only write
curl -X PUT "http://localhost:8101/providers/policies" \
  -H 'Content-Type: application/json' \
  -d '{
    "document": {"defaults":{},"openrouter":{},"budget":{},"selection":{}},
    "expected_version": "<current_version>",
    "validate_only": true,
    "reason": "validate from integration",
    "actor": "external-client"
  }' | python3 -m json.tool

# apply write
curl -X PUT "http://localhost:8101/providers/policies" \
  -H 'Content-Type: application/json' \
  -d '{
    "document": <full_policy_document_json>,
    "expected_version": "<current_version>",
    "validate_only": false,
    "reason": "update routing defaults",
    "actor": "external-client"
  }' | python3 -m json.tool

# rollback to latest successful pre-change snapshot
curl -X POST "http://localhost:8101/providers/policies/rollback" \
  -H 'Content-Type: application/json' \
  -d '{
    "expected_version": "<current_version>",
    "reason": "revert after incident",
    "actor": "external-client"
  }' | python3 -m json.tool
```

- Keep `metadata.project` populated for traceability and filtering.
- Use async mode for larger matrices.
- `compare-compact` includes provider/lane fields so mixed-candidate adjudication is explicit.
- Evaluation summary includes `estimated_cost_total_usd` only when matching model pricing metadata exists in `config/provider_models.json`.
