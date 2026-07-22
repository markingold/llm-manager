# Failed Model Retest

- Generated: 2026-05-21T02:57:57.813394+00:00
- Passed: 2
- Failed: 1

| Model | Result | Backend | Notes |
|---|---|---|---|
| LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 | PASS | tabbyapi | switch=200 test=200 |
| Qwen3.6-27B-GGUF | PASS | tgw | switch=200 test=200 |
| Qwen__Qwen3.5-9B | FAIL | - | switch=200 test=502 |

## Qwen__Qwen3.5-9B
- backend=vllm switch=500 test=None | switch_excerpt={"raw": "Internal Server Error"} | test_excerpt=
- backend=tgw switch=200 test=502 | switch_excerpt={"ok": true, "link": "/srv/2bananas/engines/text-generation-webui/user_data/models/chat_active_model", "target": "/srv/2bananas/engines/text-generation-webui/user_data/models/Qwen__Qwen3.5-9B", "mode": "chat", "backend": | test_excerpt={"detail": "chat api error: HTTPConnectionPool(host='127.0.0.1', port=8500): Max retries exceeded with url: /v1/chat/completions (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x78cf633e7e80>: Failed to establish a new connection: [Errno 111] Connection refused'))"}