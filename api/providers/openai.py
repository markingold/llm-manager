from typing import Any

import requests

try:
    from .base import ProviderAdapter
except ImportError:  # Support legacy api/ working-directory imports.
    from providers.base import ProviderAdapter


class OpenAIProviderAdapter(ProviderAdapter):
    def name(self) -> str:
        return "openai"

    def chat(self, request: dict[str, Any]) -> dict[str, Any]:
        base = str(request["base"]).rstrip("/")
        payload = dict(request["payload"])
        timeout = int(request.get("timeout", 60))
        api_key = str(request.get("api_key") or "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        r = requests.post(f"{base}/chat/completions", json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def completions(self, request: dict[str, Any]) -> dict[str, Any]:
        # Keep completions support by mapping prompt to chat completion.
        payload = dict(request["payload"])
        prompt = str(payload.get("prompt", ""))
        model = str(payload.get("model"))
        chat_payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if "max_tokens" in payload:
            chat_payload["max_tokens"] = payload["max_tokens"]
        if "temperature" in payload:
            chat_payload["temperature"] = payload["temperature"]
        if "top_p" in payload:
            chat_payload["top_p"] = payload["top_p"]
        if "stop" in payload:
            chat_payload["stop"] = payload["stop"]

        raw = self.chat({**request, "payload": chat_payload})
        text = ""
        choices = raw.get("choices", []) if isinstance(raw, dict) else []
        if choices:
            text = str(choices[0].get("message", {}).get("content", ""))
        return {
            "id": raw.get("id", ""),
            "object": "text_completion",
            "created": raw.get("created", 0),
            "model": model,
            "choices": [{"index": 0, "text": text, "finish_reason": choices[0].get("finish_reason", "stop") if choices else "stop"}],
            "usage": raw.get("usage", {}),
        }

    def embeddings(self, request: dict[str, Any]) -> dict[str, Any]:
        base = str(request["base"]).rstrip("/")
        payload = dict(request["payload"])
        timeout = int(request.get("timeout", 60))
        api_key = str(request.get("api_key") or "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        r = requests.post(f"{base}/embeddings", json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def normalize_error(self, error: Exception) -> dict[str, Any]:
        msg = str(error)
        lower = msg.lower()
        if "429" in lower or "rate" in lower:
            return {"type": "rate_limited", "message": msg, "retryable": True}
        if "401" in lower or "403" in lower:
            return {"type": "auth_error", "message": msg, "retryable": False}
        if "404" in lower:
            return {"type": "model_unavailable", "message": msg, "retryable": False}
        return {"type": "provider_error", "message": msg, "retryable": "5" in lower}
