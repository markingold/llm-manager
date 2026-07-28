import json
from typing import Any

import requests

try:
    from .base import ProviderAdapter
except ImportError:  # Support legacy api/ working-directory imports.
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
        def _contains_any(text: str, terms: list[str]) -> bool:
            return any(term in text for term in terms)

        status_code = None
        provider_code = None
        provider_type = None
        provider_message = None
        message = str(error or "OpenRouter request failed")

        if isinstance(error, requests.Timeout):
            return {
                "type": "provider_timeout",
                "message": message,
                "retryable": True,
                "status_code": None,
                "provider_code": None,
                "provider_type": None,
            }

        if isinstance(error, requests.ConnectionError):
            return {
                "type": "provider_error",
                "message": message,
                "retryable": True,
                "status_code": None,
                "provider_code": None,
                "provider_type": None,
            }

        if isinstance(error, requests.HTTPError):
            response = getattr(error, "response", None)
            if response is not None:
                status_code = int(getattr(response, "status_code", 0) or 0)
                body = None
                try:
                    body = response.json()
                except Exception:
                    body = None

                if isinstance(body, dict):
                    err = body.get("error", body)
                    if isinstance(err, list) and err:
                        err = err[0]
                    if isinstance(err, dict):
                        raw_code = err.get("code") or err.get("error_code")
                        raw_type = err.get("type") or err.get("error_type")
                        raw_message = err.get("message") or err.get("detail")
                        if raw_code is not None:
                            provider_code = str(raw_code).strip() or None
                        if raw_type is not None:
                            provider_type = str(raw_type).strip() or None
                        if raw_message is not None:
                            provider_message = str(raw_message).strip() or None
                        metadata = err.get("metadata")
                        raw_provider_error = metadata.get("raw") if isinstance(metadata, dict) else None
                        if isinstance(raw_provider_error, str) and raw_provider_error.strip():
                            try:
                                raw_body = json.loads(raw_provider_error)
                            except (TypeError, ValueError):
                                raw_body = None
                            raw_error = raw_body.get("error", raw_body) if isinstance(raw_body, dict) else None
                            if isinstance(raw_error, dict):
                                nested_message = raw_error.get("message") or raw_error.get("detail")
                                nested_code = raw_error.get("code")
                                nested_type = raw_error.get("type")
                                if nested_message is not None:
                                    provider_message = str(nested_message).strip()[:500] or provider_message
                                if nested_code is not None:
                                    provider_code = str(nested_code).strip() or provider_code
                                if nested_type is not None:
                                    provider_type = str(nested_type).strip() or provider_type

                if not provider_message:
                    text = str(getattr(response, "text", "") or "").strip()
                    provider_message = text[:500] if text else None

        combined = " ".join(
            [
                str(message or ""),
                str(provider_message or ""),
                str(provider_code or ""),
                str(provider_type or ""),
            ]
        ).strip()
        lower = combined.lower()
        final_message = str(provider_message or message or "OpenRouter request failed")

        if status_code in (401, 403):
            err_type = "auth_error"
            retryable = False
        elif status_code == 404:
            err_type = "model_unavailable"
            retryable = False
        elif status_code == 429:
            if _contains_any(lower, ["quota", "insufficient", "credit", "billing", "limit reached"]):
                err_type = "quota_exhausted"
                retryable = True
            elif _contains_any(lower, ["not free", "free tier", "payment", "paid", "requires paid"]):
                err_type = "not_free_anymore"
                retryable = False
            else:
                err_type = "rate_limited"
                retryable = True
        elif status_code in (400, 422):
            if _contains_any(
                lower,
                [
                    "context",
                    "context length",
                    "maximum context",
                    "max context",
                    "too long",
                    "token limit",
                    "max tokens",
                ],
            ):
                err_type = "context_too_large"
                retryable = False
            else:
                err_type = "invalid_request"
                retryable = False
        elif isinstance(status_code, int) and status_code >= 500:
            err_type = "provider_error"
            retryable = True
        else:
            if _contains_any(lower, ["quota", "insufficient", "credit", "billing"]):
                err_type = "quota_exhausted"
                retryable = True
            elif _contains_any(lower, ["not free", "free tier", "requires paid", "payment"]):
                err_type = "not_free_anymore"
                retryable = False
            elif _contains_any(lower, ["rate", "429"]):
                err_type = "rate_limited"
                retryable = True
            elif _contains_any(lower, ["401", "403", "unauthorized", "forbidden"]):
                err_type = "auth_error"
                retryable = False
            elif _contains_any(lower, ["404", "not found", "model not found"]):
                err_type = "model_unavailable"
                retryable = False
            elif _contains_any(lower, ["context", "max tokens", "token limit", "too long"]):
                err_type = "context_too_large"
                retryable = False
            elif _contains_any(lower, ["timeout", "timed out"]):
                err_type = "provider_timeout"
                retryable = True
            else:
                err_type = "provider_error"
                retryable = _contains_any(lower, ["5xx", "503", "502", "500", "timeout", "temporar"])

        return {
            "type": err_type,
            "message": final_message,
            "retryable": retryable,
            "status_code": status_code,
            "provider_code": provider_code,
            "provider_type": provider_type,
        }
