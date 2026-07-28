from __future__ import annotations

import copy

CONFIG_SCHEMA_VERSION = 3
MODEL_CAPABILITIES = {
    "chat",
    "completions",
    "embeddings",
    "classification",
    "structured_output",
    "tool_calling",
    "reasoning",
    "vision",
}


def _legacy_capabilities(row: dict, *, local_slot: bool = False) -> list[str]:
    raw = row.get("capabilities", [])
    capabilities = {
        str(value).strip().lower()
        for value in raw
        if isinstance(raw, list) and str(value).strip()
    }
    if "embed" in capabilities:
        capabilities.remove("embed")
        capabilities.add("embeddings")
    if "completion" in capabilities:
        capabilities.remove("completion")
        capabilities.add("completions")

    model_id = str(row.get("id", "")).strip().lower()
    if not capabilities:
        if "embedding" in model_id:
            capabilities.add("embeddings")
        else:
            capabilities.update({"chat", "completions"})
    elif local_slot and "chat" in capabilities:
        capabilities.add("completions")

    boolean_map = {
        "supports_json_schema": "structured_output",
        "supports_tools": "tool_calling",
        "supports_reasoning": "reasoning",
        "supports_vision": "vision",
    }
    for legacy_key, capability in boolean_map.items():
        if row.get(legacy_key) is True:
            capabilities.add(capability)
    return sorted(capabilities & MODEL_CAPABILITIES)


def migrate_provider_models(document: dict) -> tuple[dict, list[str]]:
    doc = copy.deepcopy(document if isinstance(document, dict) else {})
    raw_version = doc.get("schema_version", 1)
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        version = 1
    if version > CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"provider models schema {version} is newer than supported schema {CONFIG_SCHEMA_VERSION}"
        )

    applied: list[str] = []
    if version < 2:
        local = doc.setdefault("local", {})
        slots = local.setdefault("slots", [])
        for row in slots:
            if isinstance(row, dict):
                row["capabilities"] = _legacy_capabilities(row, local_slot=True)
        if not any(isinstance(row, dict) and row.get("id") == "embed" for row in slots):
            slots.append(
                {
                    "id": "embed",
                    "label": "Local Embedding Slot",
                    "enabled": True,
                    "backend": "vllm",
                    "base_env": "LLM_EMBED_API_BASE",
                    "capabilities": ["embeddings"],
                }
            )

        for provider, bucket_names in (("openrouter", ("free", "paid")), ("openai", ("allowed",))):
            root = doc.setdefault(provider, {})
            for bucket_name in bucket_names:
                for row in root.setdefault(bucket_name, []):
                    if isinstance(row, dict):
                        row["capabilities"] = _legacy_capabilities(row)
        doc["schema_version"] = 2
        applied.append("provider_models_v1_to_v2_authoritative_capabilities")
        version = 2
    if version < 3:
        doc["schema_version"] = 3
        applied.append("provider_models_v2_to_v3_evaluation_catalog")
    return doc, applied


def migrate_provider_policies(document: dict) -> tuple[dict, list[str]]:
    doc = copy.deepcopy(document if isinstance(document, dict) else {})
    raw_version = doc.get("schema_version", 1)
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        version = 1
    if version > CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"provider policies schema {version} is newer than supported schema {CONFIG_SCHEMA_VERSION}"
        )

    applied: list[str] = []
    if version < 2:
        task_overrides = doc.setdefault("task_overrides", {})
        embed = task_overrides.setdefault("embed", {})
        embed_defaults = embed.setdefault("defaults", {})
        embed_defaults.setdefault("strategy", "local_first")
        embed_selection = embed.setdefault("selection", {})
        embed_selection.setdefault("local_first", ["local", "openrouter.paid", "openai"])
        doc["schema_version"] = 2
        applied.append("provider_policies_v1_to_v2_embedding_lane")
        version = 2
    if version < 3:
        evaluation = doc.setdefault("evaluation", {})
        evaluation.setdefault("require_curated_remote", True)
        evaluation.setdefault("max_run_estimated_cost_usd", 10.0)
        evaluation.setdefault("provider_call_timeout_seconds", 45)
        evaluation.setdefault("judge_call_timeout_seconds", 90)
        scheduler = evaluation.setdefault("scheduler", {})
        scheduler.setdefault("enabled", True)
        scheduler.setdefault("daily_health_interval_hours", 24)
        scheduler.setdefault("weekly_discovery_interval_days", 7)
        scheduler.setdefault("biweekly_benchmark_interval_days", 14)
        scheduler.setdefault("startup_grace_seconds", 300)
        scheduler.setdefault("free_benchmark_repetitions", 2)
        scheduler.setdefault("free_benchmark_max_models", 30)
        scheduler.setdefault("auto_promote", True)
        promotion = evaluation.setdefault("promotion", {})
        promotion.setdefault("minimum_score", 0.70)
        promotion.setdefault("minimum_cases", 8)
        promotion.setdefault("minimum_score_delta", 0.03)
        promotion.setdefault("max_failure_rate", 0.10)
        promotion.setdefault("max_p95_latency_ms", 30000)
        promotion.setdefault("fallback_count", 3)
        doc["schema_version"] = 3
        applied.append("provider_policies_v2_to_v3_scheduled_evaluations")
    return doc, applied
