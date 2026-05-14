# LLM Manager - ExLlama And Conversion Plan

> Split from former `docs/engine-ideas.md` on 2026-03-29

## EXL3 research and fit in your ecosystem

## What EXL3 is

- EXL3 is the new quantization format introduced in ExLlamaV3.
- It is described by ExLlamaV3 as a streamlined variant of QTIP.
- The pitch is efficient low-bitrate quantization with a simpler conversion path than some heavier SOTA quantizers.
- ExLlamaV3 emphasizes that EXL3 conversion can be done from an HF-format model with a target bitrate.

## Operationally important EXL3 points

- ExLlamaV3 is the natural runtime for EXL3.
- TabbyAPI is the recommended OpenAI-compatible server for ExLlamaV3.
- EXL3 keeps much more of the original model file structure than EXL2, which should make future framework support easier.
- ExLlamaV3 explicitly notes that this opens the door for broader framework support over time, including other frameworks such as HF Transformers and vLLM.

## Why EXL3 is attractive for you

- You want support for all types of models without exploding VRAM requirements.
- EXL3 is aimed squarely at efficient consumer-GPU deployment.
- It gives you a path for very low-bitrate inference while staying in a modern OpenAI-compatible serving story through TabbyAPI.
- If you are already comfortable with EXL2, EXL3 is the natural next expansion rather than an unrelated branch.

## Why EXL3 should not be jammed into the current TGW-only launcher model

- You can technically keep forcing EXL3 through TGW where supported.
- But that is the least clean integration path because EXL3 belongs to the ExLlamaV3 ecosystem, not to the TGW loader model conceptually.
- If you want EXL3 to be a real first-class citizen, llm-manager should understand an ExLlama backend family directly.

## Why this matters for EXL3 specifically

- EXL3 is not just another extension. It is really the ExLlamaV3 lane.
- The cleanest EXL3 story is:
  - convert or download EXL3 models
  - serve them through ExLlamaV3
  - expose them via TabbyAPI
  - let llm-manager operate TabbyAPI-backed systemd units the same way it operates other slot services

## Current conversion status in this repo

- You already have a full-model EXL2 conversion path in the repo for Hugging Face style models with safetensors weights.
- The relevant script is `app/src/llm_manager/download_convert_chat_model.py`, which can download or reuse a raw HF model directory and run the ExLlama conversion script to produce an EXL2 output.
- `app/src/llm_manager/convert_lora.py` is narrower than its name first suggests: it converts merged LoRA outputs from `output/merged_<model_key>` into EXL2, not arbitrary raw base-model folders.
- So the current answer is: full-model to EXL2 exists and is now promoted to a managed llm-manager workflow for HF repo sources, while EXL3 conversion is not yet implemented as a parallel managed path.

## Implementation status (2026-05-14)

Estimated completion: about 75%.

### Completed

- Managed EXL2 conversion API flow is now implemented for both Hugging Face repo sources and merged local model sources.
- New managed endpoints are live: `/conversions/exl2`, `/conversions/exl2/jobs`, `/conversions/exl2/jobs/{job_id}`, `/conversions/exl2/artifacts`, `/conversions/exl2/artifacts/{artifact_id}`.
- Conversion run and artifact metadata are persisted in runtime state (`conversion_runs`, `conversion_artifacts`).
- Persisted metadata includes source type and source id/hash, bits, groupsize, output path/model dir, timestamps, and detected loader/kind.
- Conversion metadata now includes preservation checks for tokenizer artifacts and chat-template continuity to reduce instruct-format regressions.
- Converted artifact metadata is surfaced in `/models` and `/providers/models` and synced into provider catalog state under `local.converted_models`.

### Partially completed

- Canonical host-level output placement under `/srv/2bananas/engines` remains a deployment/layout discipline item rather than an enforced API invariant.

### Not completed

- EXL3 managed conversion path (Phase 2) is still pending.
- ExLlamaV3-native conversion metadata parity and TabbyAPI-first EXL3 lane wiring remain pending.

## Planned conversion workflow for EXL2 and EXL3

- llm-manager should explicitly support conversion as a managed workflow, not just inference and switching.
- It should support at least these source types:
  - HF-format local model directories
  - HF repo IDs that llm-manager can download first
  - merged local model outputs produced by your LoRA workflow
- It should support at least these targets:
  - EXL2 via the ExLlamaV2 conversion tooling
  - EXL3 via the ExLlamaV3 conversion tooling once that lane is installed and validated on your host
- The conversion workflow should persist conversion metadata so llm-manager can later show:
  - source model or repo
  - source revision if known
  - target format
  - target bitrate or bpw
  - groupsize or equivalent quant parameters
  - conversion tool version
  - output path
  - output size
  - conversion timestamp
- Converted outputs should be written to canonical model storage under `/srv/2bananas/engines`, not scattered inside the llm-manager repo.
- llm-manager may keep job metadata, logs, and pointers locally, but converted EXL2 and EXL3 artifacts themselves should live in the centralized engine and model area.
- That metadata should become part of the model catalog so the router and UI know whether a model is original, merged, EXL2-converted, or EXL3-converted.
- For EXL3 specifically, do not hide the conversion step behind TGW assumptions. It should be modeled as an ExLlamaV3-native workflow that feeds the TabbyAPI backend lane.

## Capability fields that matter for the ExLlama lane

- `format`: exl2, exl3, gguf, awq, gptq, transformers, lora
- `recommended_backend`
- `fallback_backends`
- `api_style`: openai
- `supports_embeddings`
- `supports_tool_calling`
- `supports_multimodal`
- `supports_lora_runtime`
- `supports_structured_output`

## Concrete changes for the conversion track

## Phase 1: managed EXL2 conversion

1. Promote existing EXL2 conversion support into a first-class llm-manager workflow instead of leaving it as a standalone helper script. (implemented)
2. Support converting raw HF-format model directories and merged local model directories into EXL2. (implemented)
3. Write converted outputs into the centralized `/srv/2bananas/engines` model area and record canonical paths in metadata.
4. Persist conversion metadata so converted models can be cataloged, inspected, and reused by the router and UI. (implemented)
5. Add admin endpoints or CLI commands to submit, monitor, and inspect conversion jobs. (implemented)

## Phase 2: EXL3 conversion enablement

1. Add an EXL3 conversion path backed by ExLlamaV3 tooling once the host-side toolchain is installed and validated.
2. Treat EXL3 outputs as first-class model catalog entries with the same metadata discipline as EXL2 outputs.
3. Keep EXL3 aligned with the TabbyAPI-backed ExLlama lane rather than treating it as a TGW-only loader feature.
4. Decide whether EXL3 conversion should share the same job model as EXL2 conversion immediately or remain a separate workflow while the toolchain matures.

## Operational caution

- ExLlamaV3 LoRA support is still a caution area.
- That means EXL3 should be treated first as an inference lane, not automatically as the primary path for LoRA-heavy workflows.
- The boundary between EXL3 inference support and LoRA-capable operational paths should be made explicit during implementation.
