from typing import Any

import requests

try:
    from .base import ProviderAdapter
except ImportError:  # Support legacy api/ working-directory imports.
    from providers.base import ProviderAdapter


class LocalProviderAdapter(ProviderAdapter):
    def name(self) -> str:
        return "local"

    def chat(self, request: dict[str, Any]) -> dict[str, Any]:
        base = str(request["base"]).rstrip("/")
        payload = dict(request["payload"])
        timeout = int(request.get("timeout", 45))

        r = requests.post(f"{base}/v1/chat/completions", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def completions(self, request: dict[str, Any]) -> dict[str, Any]:
        base = str(request["base"]).rstrip("/")
        payload = dict(request["payload"])
        timeout = int(request.get("timeout", 45))

        r = requests.post(f"{base}/v1/completions", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def embeddings(self, request: dict[str, Any]) -> dict[str, Any]:
        base = str(request["base"]).rstrip("/")
        payload = dict(request["payload"])
        timeout = int(request.get("timeout", 45))

        r = requests.post(f"{base}/v1/embeddings", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def normalize_error(self, error: Exception) -> dict[str, Any]:
        msg = str(error)
        return {
            "type": "provider_error",
            "message": msg,
            "retryable": "timed out" in msg.lower() or "5" in msg,
        }
