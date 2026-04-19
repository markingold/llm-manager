# LLM Manager - Technology Watchlist

> Consolidated: 2026-03-29

| Technology | Why It Matters | Prerequisite | Priority |
|-----------|---------------|-------------|----------|
| vLLM | Strong candidate for throughput-oriented local serving, structured outputs, embeddings, and modern HF-family serving. | Backend launcher abstraction and backend-aware model inspection. | high |
| TabbyAPI | OpenAI-compatible serving layer for the ExLlama lane and the cleanest path for EXL2 and EXL3 model serving. | ExLlama lane integration and launcher support. | high |
| ExLlamaV3 / EXL3 | Enables lower-bitrate consumer-GPU inference and a future-facing quantization path beyond EXL2. | Host-side ExLlamaV3 tooling install, conversion validation, and TabbyAPI integration. | high |
| OpenRouter free and paid catalogs | Expands llm-manager from local orchestration into curated remote-model brokering. | Provider adapters, policy engine, rate limiting, and catalog storage. | high |
| OpenAI API integration | Provides a premium provider lane and a stable quality fallback. | Provider adapters, secrets, and routing policy support. | high |
| Direct llama.cpp backend | Could simplify GGUF serving and reduce TGW dependence for that format. | Decide whether GGUF remains TGW-backed in the near term and add a direct backend abstraction. | medium |
| FlashInfer | Useful as a performance capability within the vLLM lane rather than a managed engine. | vLLM diagnostics and capability reporting. | medium |
| OpenAI Responses-style API support | The provider abstraction already anticipates `responses(request)` as a possible future surface. | Stable normalized request model and a decision to expose more than chat/completions first. | low |
| Raw Transformers fallback backend | Could preserve flexibility for architectures that lag behind the preferred serving stacks. | Proof that the experimental path is worth productizing and a non-interactive launcher design. | low |
