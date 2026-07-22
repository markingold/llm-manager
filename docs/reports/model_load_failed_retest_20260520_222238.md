# Failed-Model Retest - 20260520_222238

- Generated at (UTC): 2026-05-21T03:25:35.913968+00:00
- Prompt check: `Reply with exactly: ping`
- PASS: 1
- FAIL: 2

| Model | Backend | Switch | Chat | Notes |
|---|---|---:|---:|---|
| LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 | tabbyapi | 200 | PASS | answer=ping |
| Qwen3.6-27B-GGUF | tgw | 200 | FAIL | {"ok": true, "link": "/srv/2bananas/engines/text-generation-webui/user_data/models/chat_active_model", "target": "/srv/2 |
| Qwen__Qwen3.5-9B | vllm | 422 | FAIL | model 'Qwen__Qwen3.5-9B' cannot be loaded in this deployment: Multimodal checkpoints are not currently supported by local backends (tgw, vllm, tabbyapi) in this deployment. |

## Key finding

- `Qwen__Qwen3.5-9B` now fails fast with HTTP 422 and does not mutate active slot state, because it is detected as a multimodal checkpoint with no compatible local backend in this deployment.
- Service health was restored to `Qwen3.5-4B-Q6_K` on `tgw` after retest.
