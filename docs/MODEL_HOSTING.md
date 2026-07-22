# Model hosting backends

Last reviewed: 2026-07-22

LLM Manager is a control plane. It does not run inference itself; each local
slot starts one inference server and exposes its OpenAI-compatible HTTP API.

## Integrated backends

| Backend | Best use here | Formats accepted by the manager | Current host |
|---|---|---|---|
| TabbyAPI | Low-latency NVIDIA serving for ExLlama quants | EXL2, EXL3 on the pinned host revision | Working on chat `:8500` |
| text-generation-webui (TGW) | Interactive workbench and broad fallback | GGUF, EXL3, standard Transformers | Working; standalone WebUI/API also running |
| vLLM | Throughput-oriented serving, embeddings, AWQ/GPTQ and standard HF models | Transformers, AWQ, GPTQ | Installed; no embedding model/unit active |

TabbyAPI is a specialized server, not merely a TGW loader. The installed May
2026 revision successfully serves EXL2, but current upstream `main` has removed
EXL2 and directs those users to its `exl2-checkpoint` branch. Do not upgrade
this host blindly while EXL2 is required. TGW is a WebUI and
compatibility facade over several loaders. On this host, TGW's GGUF path starts
its bundled `llama-server`; direct llama.cpp is not yet a first-class manager
backend. vLLM is the dedicated batching and embedding lane.

The compatibility matrix intentionally fails closed. In particular, current
TGW releases no longer expose an ExLlamaV2 loader, and a raw PEFT/LoRA adapter
directory is not a standalone checkpoint. The manager does not advertise a
format unless its launcher has a complete load path.

## Other viable servers

| Server | Why consider it | Cost / reason to defer |
|---|---|---|
| llama.cpp server | Lean, dependable GGUF server with OpenAI-compatible chat, embeddings and reranking; CPU/GPU offload | Highest-value next backend, but needs its own lifecycle/readiness adapter rather than hiding under TGW |
| SGLang | High-throughput language/multimodal serving, structured generation, prefix caching, broad accelerator support | Overlaps vLLM; install only after a representative benchmark shows a material benefit |
| TensorRT-LLM | NVIDIA-specific optimized engines, in-flight batching and quantization | More engine-build/deployment complexity; best for stable, high-volume model fleets |
| Ollama | Very simple downloads and local model lifecycle with partial OpenAI compatibility | Duplicates LLM Manager's catalog/lifecycle and offers less explicit runtime control |
| Hugging Face TGI / LocalAI | Mature serving alternatives for particular deployments | Additional operational surface without covering a current gap better than vLLM + llama.cpp |

## Recommended layout for this machine

This host has two RTX 3090 24 GB GPUs:

1. Keep TabbyAPI for EXL2/EXL3 chat models.
2. Keep TGW for interactive testing and as the current GGUF lane.
3. Finish the vLLM embedding service/model configuration, then use vLLM for
   standard Hugging Face, AWQ, and GPTQ checkpoints.
4. Add direct llama.cpp next if GGUF is operationally important. It removes the
   TGW layer from production GGUF serving and can later provide embedding or
   reranking lanes.
5. Benchmark SGLang against vLLM before installing it. Defer TensorRT-LLM and
   Ollama unless a concrete workload requires them.

Upstream references:

- [TextGen (formerly text-generation-webui)](https://github.com/oobabooga/textgen)
- [TabbyAPI](https://github.com/theroyallab/tabbyAPI)
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/stable/serving/openai_compatible_server/)
- [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [SGLang](https://github.com/sgl-project/sglang)
- [TensorRT-LLM](https://docs.nvidia.com/tensorrt-llm/)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
