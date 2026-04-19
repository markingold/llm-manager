# LLM Manager - Local Backend Architecture Plan

> Split from former `docs/engine-ideas.md` on 2026-03-29

## Executive Summary

The cleanest way to make llm-manager more accepting of different model types is not to keep stretching text-generation-webui as the one true runtime. The codebase already has the right abstraction boundary for something better: each slot talks to an OpenAI-compatible API base, and systemd units are already treated as the real engine control plane.

Recommended direction:

1. Keep text-generation-webui as a compatibility backend and UI-oriented sandbox.
2. Add vLLM as a first-class backend for high-throughput HF, AWQ, GPTQ, GGUF, embeddings, classification, and structured-output workloads.
3. Add TabbyAPI as a first-class backend for ExLlama-backed EXL2 and EXL3 workloads.
4. Change llm-manager from "pick TGW loader" to "pick backend plus model format plus slot policy".

That yields a more durable local serving stack:

- EXL2 and EXL3: TabbyAPI / ExLlama path
- AWQ and GPTQ: vLLM first, TGW fallback
- GGUF: llama.cpp through TGW today, possibly direct later
- HF fp16/bf16 multimodal and newer architectures: vLLM first, TGW fallback
- Legacy or oddball cases: TGW compatibility lane

## Canonical filesystem policy

- The canonical home for engine installs, engine virtual environments, and model artifacts should remain `/srv/2bananas/engines`.
- llm-manager should remain the control plane and metadata layer, not the long-term storage home for engines or model weights.
- If engine components need cleanup or reinstallation for consistency, the plan should assume that is acceptable as long as the canonical layout under `/srv/2bananas/engines` becomes clearer and more reproducible afterward.
- Symlinks from the llm-manager project into centralized engine or model locations are acceptable for compatibility, runtime aliases, or active-slot switching, but the source of truth should stay in `/srv/2bananas/engines`.
- The project should avoid silently drifting into duplicate copies of large engine installs or model directories under `/srv/2bananas/projects/llm-manager`.

### Recommended directory intent

- `/srv/2bananas/engines/<engine-name>` for engine source trees or managed installs
- `/srv/2bananas/engines/<engine-name>-env` for engine-specific virtual environments where needed
- `/srv/2bananas/engines/models` or another clearly designated subdirectory under `/srv/2bananas/engines` for canonical model storage
- symlinked active-model paths or runtime aliases may point into those canonical locations, but should not become a second unmanaged storage layer

### What llm-manager should own locally

- API code
- router and backend configuration
- lightweight metadata and persistent state
- job logs and evaluation artifacts
- optional symlinks or runtime pointers, not canonical engine binaries or model weight stores

## What I Found In `/srv/2bananas/engines`

### Clearly relevant to llm-manager

#### text-generation-webui

- This is the current runtime center of gravity for llm-manager.
- llm-manager already launches models through `run/engine_launcher.py`, which resolves a model format and then starts `server.py` with a TGW loader.
- Current model inspection already recognizes EXL2, EXL3, GGUF, AWQ, GPTQ, transformers, and LoRA formats.
- This remains useful as a broad compatibility layer and as a UI and tooling surface.

#### exllamav2

- There is a full ExLlamaV2 source checkout in `/srv/2bananas/engines/exllamav2`.
- The local llm-manager docs already assume ExLlama-derived support for EXL2, EXL3, AWQ, and GPTQ via TGW loader selection.
- However, `exllamav2` did not appear installed in `/srv/2bananas/engines/llm-env`, so this currently looks more like a source checkout than an actively integrated standalone serving backend.
- ExLlamaV2 by itself is a library, not the cleanest API server surface for llm-manager.

#### `vllm-env` plus `run_vllm` scripts

- You already have a real vLLM setup, not just a placeholder.
- `/srv/2bananas/engines/vllm-env` contains `vllm 0.11.0`.
- It also contains `flashinfer-python 0.5.0`, so FlashInfer is already part of the vLLM lane on this host.
- `/srv/2bananas/engines/run_vllm_qwen.sh` and `/srv/2bananas/engines/run_vllm_qwen_awq.sh` show active intent to serve AWQ and GPTQ Qwen models via vLLM on ports `8500` and `8501`.
- This is the strongest existing candidate for first-class incorporation.

#### flashinfer

- This is a full FlashInfer source checkout.
- FlashInfer is not a standalone engine. It is a kernel library for LLM serving.
- Its value to llm-manager is indirect: it can accelerate other engines, especially vLLM.
- Because `flashinfer-python` is already installed in `vllm-env`, you are already getting practical value from it through vLLM.

### Adjacent but not primary llm-manager backends

#### `run_llm_server.py`

- This is an ad hoc FastAPI and Transformers server with an interactive terminal menu and manual quantization choices.
- It is useful as a one-off experiment.
- It is not a good direct fit for llm-manager because it is interactive, single-purpose, and not aligned with your systemd-first slot model.
- If anything from it is worth keeping, it is the idea of a lightweight raw-Transformers fallback backend.

#### `phi4-env`

- This looks like an experiment-specific Transformers environment.
- It has `transformers 4.57.1` installed.
- `bitsandbytes` did not appear present there, so it does not currently look like a well-rounded quantized production lane.
- Treat this as an experiment environment, not an integration target.

### Probably out of scope for llm-manager

#### comfyui

- Useful for image generation workflows, not for the current llm-manager model-serving role.
- Could become relevant only if llm-manager grows into a general local AI orchestrator.

#### home-assistant

- This is a consumer of local LLM services, not a serving backend for them.
- It matters as an integration client, not as an llm-manager engine.

#### `llm-env` and other env directories

- These are implementation details around installed toolchains.
- They should not be treated as engines themselves.

## What llm-manager looks like today

Right now llm-manager is already halfway to a backend-agnostic design, but not all the way there.

### The good news

- The API routes by slot, not by backend implementation.
- Each slot already points to its own OpenAI-compatible base URL.
- Engine control is systemd-first.
- Model switching is symlink-based and decoupled from prompt routing.
- The current host already reflects the desired direction that engines and runtime model paths live outside the llm-manager repo.

### The limiting assumption

- `run/engine_launcher.py` still assumes the runtime process is TGW.
- `api/model_inspector.py` currently ends at "which TGW loader should I use?"
- In practice, format support is being expressed as loader selection, not backend selection.

That is the part to change.

## Clean incorporation opportunities

## vLLM should become a first-class backend

### Why it fits

- You already have it installed and you already have scripts serving AWQ models with it.
- It exposes an OpenAI-compatible API, which matches llm-manager's current per-slot contract.
- It supports much more than plain chat completions now: structured outputs, tool calling, embeddings, classification, scoring, tokenizer endpoints, and multimodal serving.
- It supports a broad quantization matrix including AWQ, GPTQ, GGUF, bitsandbytes, FP8, and more.
- It is a stronger fit than TGW for throughput-oriented serving and newer model families.

### Best use in your stack

- Make vLLM the preferred backend for:
  - AWQ models
  - GPTQ models
  - HF fp16 and bf16 models when architecture support exists
  - embedding models
  - classification and scoring models
  - structured-output or tool-calling workloads where throughput matters

### What this changes in llm-manager

- Add a backend type such as `vllm` in model metadata.
- Launch slot units through a vLLM-specific launcher instead of the TGW launcher when selected.
- Allow model inspection to recommend `backend = vllm` rather than only `loader = exllamav2` or `loader = transformers`.

### Why this is better than routing AWQ and GPTQ through TGW only

- The current inspector maps AWQ and GPTQ to TGW's `exllamav2` loader.
- That works, but it leaves performance and feature coverage on the table because you already have a native vLLM path on this host.
- The existing vLLM scripts are concrete evidence that part of your stack already wants this split.

## TabbyAPI should become the EXL2 and EXL3 backend

### Why it fits

- TabbyAPI is the official API server for ExLlamaV2 and ExLlamaV3.
- It is OpenAI-compatible, which means llm-manager can treat it like any other slot backend.
- It supports EXL2, EXL3, GPTQ, and FP16 through the ExLlama backends.
- It has explicit support for model load and unload, LoRA load and unload, embeddings, templates, and sampler overrides.

### Best use in your stack

- Make TabbyAPI the preferred backend for:
  - EXL2 models
  - EXL3 models
  - consumer-GPU-first inference where ExLlama performs especially well
  - aggressive low-bitrate quant deployments

### Important caution

- ExLlamaV3 is advancing quickly, but its own README still lists LoRA support as missing.
- That means EXL3 should be treated as a strong inference lane, not necessarily the best immediate lane for LoRA-heavy workflows.
- For LoRA-heavy workloads, vLLM or TGW may still be the smoother operational choice depending on model family.

## Keep text-generation-webui, but demote it from universal runtime to compatibility lane

### Why keep it

- It already works in your stack.
- It supports multiple backends, including llama.cpp, Transformers, ExLlamaV3, and TensorRT-LLM in upstream current docs.
- It offers a broad UI and tool surface.
- It remains useful for experimentation, manual debugging, and odd formats that do not justify a dedicated backend path.

### Why not keep it as the only runtime abstraction

- llm-manager is operational software, not just a personal UI launcher.
- Once you want multiple serving stacks, the TGW-specific loader abstraction becomes the wrong level of control.
- You want llm-manager to choose between backends, not just between TGW flags.

## FlashInfer should stay an implementation dependency, not a top-level llm-manager engine

### Why

- FlashInfer is a kernel layer, not a user-facing API server.
- llm-manager should not manage it as a service.
- It should instead record whether a backend can benefit from it.

### Practical use

- Treat FlashInfer as a capability of the vLLM lane.
- If you later add backend diagnostics, report something like:
  - backend: vllm
  - acceleration: flashinfer available

## Proposed llm-manager design change

## Replace loader recommendation with backend recommendation

Today the model inspector answers:

- what kind of model is this?
- which TGW loader should launch it?

It should answer:

- what kind of model is this?
- which backend should serve it?
- what launch args or unit template does that backend need?
- what capabilities does this model and backend pair expose?

### Suggested backend enum

- `tgw`
- `vllm`
- `tabbyapi`
- optional later: `llamacpp-direct`

### Suggested launch abstraction

Instead of one TGW launcher, have one backend launcher per engine family:

- `run/launch_tgw.py`
- `run/launch_vllm.py`
- `run/launch_tabbyapi.py`

Each launcher should accept normalized inputs such as:

- slot name
- model path
- port
- max sequence length
- gpu assignment
- optional backend-specific overrides

That keeps the systemd layer clean.

## Suggested backend routing rules

| Model kind | Preferred backend | Fallback | Notes |
| --- | --- | --- | --- |
| EXL2 | TabbyAPI | TGW | Best aligned with ExLlama ecosystem |
| EXL3 | TabbyAPI | TGW if proven stable on your host | Treat as ExLlamaV3-native |
| AWQ | vLLM | TGW | You already have working vLLM scripts for this |
| GPTQ | vLLM | TabbyAPI or TGW | Depends on architecture support and local testing |
| GGUF | TGW llama.cpp today | direct llama.cpp later | Current stack already recognizes GGUF via TGW |
| FP16 and BF16 HF | vLLM | TGW transformers | Prefer vLLM when supported |
| LoRA runtime | vLLM or TGW | TabbyAPI only if feature parity is confirmed | ExLlamaV3 LoRA status is still a caution area |
| Embeddings | vLLM or TabbyAPI | none | Both have explicit API stories |

## Concrete changes for the local backend track

## Implementation progress snapshot

As of 2026-03-29:
- `api/model_inspector.py` now emits `recommended_backend` and `fallback_backends`
- `POST /switch` now accepts optional `backend` (`tgw|vllm|tabbyapi`)
- Slot backend preference is persisted in `run/state/slot_backends.json`
- backend launcher wrappers were added at `run/launch_tgw.py`, `run/launch_vllm.py`, and `run/launch_tabbyapi.py`
- `run/engine_launcher.py` remains stable as a compatibility entrypoint and delegates to `run/launch_tgw.py`

## Phase 1: low-risk structural work

1. Extend `model_inspector.py` to emit `recommended_backend` and `fallback_backends`.
2. Add a backend field to switch or slot configuration.
3. Introduce launcher wrappers for TGW, vLLM, and TabbyAPI.
4. Keep all existing API routes and web UI slot controls unchanged.
5. Standardize and document the canonical engine and model directory layout under `/srv/2bananas/engines` before expanding backend coverage.

## Phase 2: vLLM integration

1. Add systemd unit templates or launcher support for vLLM slots.
2. Support serving AWQ and GPTQ models directly through vLLM.
3. Add optional endpoints or metadata for embeddings and classification-capable slots.

## Phase 3: TabbyAPI integration for EXL2 and EXL3

1. Add a TabbyAPI launcher path.
2. Add EXL2 and EXL3 model handling as first-class backends.
3. Add optional model load and unload support if backend-native switching is needed instead of symlink-only switching for those slots.

## Phase 4: capability-aware UI

1. Show slot backend in `/engines/status`.
2. Show model format and backend recommendation in the dashboard.
3. Hide unsupported operations per backend, instead of pretending every slot can do the same thing.

## What not to incorporate right now

- Do not turn FlashInfer into a managed engine.
- Do not integrate `run_llm_server.py` as-is.
- Do not fold ComfyUI or Home Assistant into llm-manager unless the project intentionally broadens into a general local AI orchestrator.
- Do not make EXL3 depend on TGW-only support when TabbyAPI is the cleaner architectural fit.
