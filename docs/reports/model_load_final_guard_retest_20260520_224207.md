# Final Guard Retest - 20260520_224207

- Generated at (UTC): 2026-05-21T03:42:07.889157+00:00
- Focus model: `Qwen__Qwen3.5-9B`

## Result

- `/switch` status: `422`
- Detail: `model 'Qwen__Qwen3.5-9B' cannot be loaded in this deployment: Multimodal checkpoints are not currently supported by local backends (tgw, vllm, tabbyapi) in this deployment.`
- Active chat model unchanged: `/srv/2bananas/engines/models/Qwen3.5-4B-Q6_K` -> `/srv/2bananas/engines/models/Qwen3.5-4B-Q6_K`
- Active chat backend unchanged: `tgw` -> `tgw`
- Post-check `/test-chat` status: `200` answer: `ping`

This confirms unsupported-model requests now fail fast (HTTP 422) without destabilizing the running chat lane.
