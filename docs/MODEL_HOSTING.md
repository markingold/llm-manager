# Model hosting backends

Last reviewed: 2026-07-22

LLM Manager is a control plane. It does not run inference itself; each local
slot starts one inference server and exposes its OpenAI-compatible HTTP API.

## Integrated backends

| Backend | Best use here | Formats accepted by the manager | Current host |
|---|---|---|---|
| TabbyAPI | Low-latency NVIDIA serving for ExLlama quants | EXL2, EXL3 on the pinned host revision | Working on chat `:8500` |
| text-generation-webui (TGW) | Interactive workbench and broad fallback | GGUF, EXL3, standard Transformers | Working; standalone WebUI/API also running |
| vLLM | Throughput-oriented serving, embeddings, LoRA, AWQ/GPTQ and standard HF models | Transformers, AWQ, GPTQ, managed PEFT adapters | Working; pinned BGE embedding lane on `:8503` |
| llama.cpp server | Lean GGUF serving with explicit GPU offload and OpenAI compatibility | GGUF | Integrated and live-smoke-tested with Qwen3.5 4B |

TabbyAPI is a specialized server, not merely a TGW loader. The installed May
2026 revision successfully serves EXL2. It is locked in
`config/backend-pins.json` at commit
`857f9e21dde2b7f10551ed5c5b9845e129b41e6a`; startup fails if the checkout no
longer matches. Current upstream `main` removed EXL2, so upgrades must first
move the lane to upstream's preserved EXL2 branch and be regression-tested.
TGW remains the interactive workbench. Direct llama.cpp is the preferred
production GGUF path, while vLLM owns batching, embeddings, and managed LoRA.

The compatibility matrix intentionally fails closed. In particular, current
TGW releases no longer expose an ExLlamaV2 loader. A PEFT/LoRA adapter is
advertised only when `base_model_name_or_path` resolves to a real checkpoint
inside the managed models root; vLLM then launches the base with the named
adapter and readiness must report that adapter identity.

The local embedding checkpoint is `BAAI/bge-small-en-v1.5`, pinned at revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`. It produces 384-dimensional
vectors, uses a 512-token context, and runs as `llm-embed.service` on GPU 1
with `--runner pooling` and a `0.20` vLLM memory-utilization ceiling.

## Other viable servers

| Server | Why consider it | Cost / reason to defer |
|---|---|---|
| SGLang | High-throughput language/multimodal serving, structured generation, prefix caching, broad accelerator support | Overlaps vLLM; install only after a representative benchmark shows a material benefit |
| TensorRT-LLM | NVIDIA-specific optimized engines, in-flight batching and quantization | More engine-build/deployment complexity; best for stable, high-volume model fleets |
| Ollama | Very simple downloads and local model lifecycle with partial OpenAI compatibility | Duplicates LLM Manager's catalog/lifecycle and offers less explicit runtime control |
| Hugging Face TGI / LocalAI | Mature serving alternatives for particular deployments | Additional operational surface without covering a current gap better than vLLM + llama.cpp |

## Recommended layout for this machine

This host has two RTX 3090 24 GB GPUs:

1. Keep TabbyAPI for EXL2/EXL3 chat models.
2. Keep standalone TGW for interactive testing; it intentionally remains bound
   to `0.0.0.0` without authentication by operator decision.
3. Use direct llama.cpp for GGUF production slots.
4. Keep the BGE embedding lane on vLLM and use vLLM for standard Hugging Face,
   AWQ, GPTQ, and resolvable PEFT adapters.
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
