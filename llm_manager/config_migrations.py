from __future__ import annotations

import copy

CONFIG_SCHEMA_VERSION = 2
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
    return doc, applied
