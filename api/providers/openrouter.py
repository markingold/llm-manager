from typing import Any
import requests

from providers.base import ProviderAdapter


class OpenRouterProviderAdapter(ProviderAdapter):
    def name(self) -> str:
        return "openrouter"

    def chat(self, request: dict[str, Any]) -> dict[str, Any]:
        base = str(request["base"]).rstrip("/")
        payload = dict(request["payload"])
        timeout = int(request.get("timeout", 60))
        api_key = str(request.get("api_key") or "")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        referer = request.get("http_referer")
        app_title = request.get("x_title")
        if referer:
            headers["HTTP-Referer"] = str(referer)
        if app_title:
            headers["X-Title"] = str(app_title)

        r = requests.post(f"{base}/chat/completions", json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def completions(self, request: dict[str, Any]) -> dict[str, Any]:
        # OpenRouter is chat-first. Map completion prompt to chat and normalize.
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
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        referer = request.get("http_referer")
        app_title = request.get("x_title")
        if referer:
            headers["HTTP-Referer"] = str(referer)
        if app_title:
            headers["X-Title"] = str(app_title)

        r = requests.post(f"{base}/embeddings", json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def normalize_error(self, error: Exception) -> dict[str, Any]:
        msg = str(error)
        lower = msg.lower()
        if "quota" in lower or "insufficient" in lower:
            return {"type": "quota_exhausted", "message": msg, "retryable": True}
        if "not free" in lower or "free tier" in lower or "payment" in lower:
            return {"type": "not_free_anymore", "message": msg, "retryable": False}
        if "429" in lower or "rate" in lower:
            return {"type": "rate_limited", "message": msg, "retryable": True}
        if "401" in lower or "403" in lower:
            return {"type": "auth_error", "message": msg, "retryable": False}
        if "404" in lower:
            return {"type": "model_unavailable", "message": msg, "retryable": False}
        return {"type": "provider_error", "message": msg, "retryable": "5" in lower}
