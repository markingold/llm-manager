# Full Model Load Pass

- Generated: 2026-05-21T01:08:01.365050+00:00
- Models tested: 10
- Passed: 7
- Failed: 3

| Model | Kind | Result | Backend | Notes |
|---|---|---|---|---|
| Gemma-3-27B-IT-EXL2-4.0bpw | exl2 | PASS | tgw | switch=200 test=200 |
| LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 | exl2 | FAIL | - | switch=500 test=None |
| Phi-4-mini-instruct | transformers | PASS | vllm | switch=200 test=200 |
| Qwen3.5-4B-Q6_K | gguf | PASS | tgw | switch=200 test=200 |
| Qwen3.5-9B-Q4_K_M | gguf | PASS | tgw | switch=200 test=200 |
| Qwen3.6-27B-GGUF | gguf | FAIL | - | switch=500 test=None |
| Qwen__Qwen3.5-9B | transformers | FAIL | - | switch=200 test=502 |
| llama3.1-8B_exl2_b6p5 | exl2 | PASS | tabbyapi | switch=200 test=200 |
| meta-llama__Llama-3.2-3B-Instruct | transformers | PASS | vllm | switch=200 test=200 |
| meta-llama__Llama-3.2-3B-Instruct_exl3_b4p5 | exl3 | PASS | tabbyapi | switch=200 test=200 |

## Failure Details

### LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2
- backend=tabbyapi switch=500 test=None | switch_excerpt={"raw": "Internal Server Error"} | test_excerpt=
- backend=tgw switch=500 test=None | switch_excerpt={"raw": "Internal Server Error"} | test_excerpt=

### Qwen3.6-27B-GGUF
- backend=tgw switch=500 test=None | switch_excerpt={"raw": "Internal Server Error"} | test_excerpt=

### Qwen__Qwen3.5-9B
- backend=vllm switch=200 test=502 | switch_excerpt={"ok": true, "link": "/srv/2bananas/engines/text-generation-webui/user_data/models/chat_active_model", "target": "/srv/2bananas/engines/text-generation-webui/user_data/models/Qwen__Qwen3.5-9B", "mode": "chat", "backend": | test_excerpt={"detail": "chat api error: HTTPConnectionPool(host='127.0.0.1', port=8500): Max retries exceeded with url: /v1/chat/completions (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x78cf63467af0>
- backend=tgw switch=200 test=502 | switch_excerpt={"ok": true, "link": "/srv/2bananas/engines/text-generation-webui/user_data/models/chat_active_model", "target": "/srv/2bananas/engines/text-generation-webui/user_data/models/Qwen__Qwen3.5-9B", "mode": "chat", "backend": | test_excerpt={"detail": "chat api error: HTTPConnectionPool(host='127.0.0.1', port=8500): Max retries exceeded with url: /v1/chat/completions (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x78cf6335cfd0>
