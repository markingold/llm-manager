# Creekwatch local route recovery — 2026-07-20

## Scope

Creekwatch Phase 9 required a healthy local-only `/router/chat` path. No external
provider was authorized or used during this recovery.

## Root cause

The chat slot selected
`groxaxo_Huihui-Qwen3.5-9B-abliterated-exl3-6.00bpw`. The current model
inspection path classified this conditional Qwen 3.5 checkpoint as multimodal
before considering its EXL3 quantization metadata. The slot consequently
launched it through the generic Transformers loader. Startup appeared healthy,
but generation produced approximately zero tokens per second; one ten-token
request occupied 277.41 seconds. During the controlled switch, the old process
also remained in an uninterruptible GPU-driver wait until systemd's kill timeout.

## Recovery

- Preserved the pre-change slot/backend state and `.env` under `/tmp`.
- Used the existing `/switch` contract to select the already-approved
  `Qwen3.5-4B-Q6_K` GGUF for the chat slot with the TGW/llama.cpp path.
- Allowed one controlled `llm-a.service` restart to clear the wedged process.
- Restored and verified the original manager `.env`; Creekwatch continues to use
  the dedicated chat slot on port 8500 through LLM Manager.
- Normalized raw consumer JSON Schemas to the standard named/strict provider
  response-format envelope. The active TGW extension does not enforce that
  envelope, so consumers must still validate output and use a bounded repair.

## Verification

- Direct chat-slot probe returned `OK` from port 8500.
- Three fallback-disabled `/router/chat` probes succeeded through LLM Manager in
  1,639 ms, 2,551 ms, and 752 ms.
- Each trace selected provider `local`, used model alias `chat_active_model`, made
  one attempt, reported `used_fallback=false`, and recorded no cost.
- `PYTHONPATH=api python3 -m pytest api/router/test_contracts.py -q` passed (2 tests).

The generic route, model alias, and response contract remain unchanged for the
other active consumers of the chat slot.
