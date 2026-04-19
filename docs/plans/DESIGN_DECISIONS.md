# LLM Manager - Design Decisions Log

> Consolidated: 2026-03-29

## Active Decisions

| # | Decision | Context | Date | Status |
|---|----------|---------|------|--------|
| 1 | LLM Manager should evolve from a local engine controller into a universal provider-and-backend broker. | The plan explicitly expands the product boundary beyond local slots to include OpenRouter and OpenAI while preserving local routing. | 2026-03-29 | Active |
| 2 | text-generation-webui remains in the stack as a compatibility lane, not the primary runtime abstraction. | TGW already works and supports odd formats, but backend choice needs to happen above loader flags. | 2026-03-29 | Active |
| 3 | vLLM is the preferred backend for AWQ, GPTQ, and supported HF-format throughput-oriented workloads. | The host already has a working vLLM environment and launch scripts, and the plan favors it for performance and feature breadth. | 2026-03-29 | Active |
| 4 | TabbyAPI is the preferred backend for EXL2 and EXL3 through the ExLlama lane. | EXL3 is treated as ExLlamaV3-native, and TabbyAPI provides the OpenAI-compatible serving surface for that lane. | 2026-03-29 | Active |
| 5 | Model inspection should recommend backends and capabilities, not only TGW loaders. | Loader-only inspection blocks multi-backend orchestration. | 2026-03-29 | Active |
| 6 | OpenRouter free traffic should be protectively throttled to 20 requests per minute with queueing and policy-driven overflow handling. | Avoiding upstream rate-limit churn is preferred over reacting after failures. | 2026-03-29 | Active |
| 7 | Conversion is part of the product surface, not just a helper-script concern. | The repo already has EXL2 conversion helpers, and the plan promotes conversion metadata and job management into first-class workflows. | 2026-03-29 | Active |
| 8 | Evaluation belongs beside the provider router rather than inside a backend adapter. | Comparative testing spans local and remote providers and must remain backend-agnostic. | 2026-03-29 | Active |
| 9 | Routing should be policy-driven with normalized requests, explicit provider preferences, and explainable decision logs. | Silent magic becomes unmanageable once routing spans local and remote providers. | 2026-03-29 | Active |
| 10 | Projects that call LLM Manager should not need direct provider API keys when LLM Manager is acting as the broker. | Centralizing provider access simplifies client integration and enforces routing policy in one place. | 2026-03-29 | Active |
| 11 | Canonical engine installs and model artifacts should live under `/srv/2bananas/engines`, while llm-manager remains the control plane, metadata layer, and UI. | The project should avoid duplicating large engine and model assets inside the repo, while still allowing symlinks or runtime pointers for compatibility. | 2026-03-29 | Active |
| 12 | Upgrade completion requires a full end-to-end evaluation pass across all local models on this host, with manual model-format downloads allowed when coverage gaps are found. | Final acceptance must reflect real local model coverage, not only schema or endpoint readiness. | 2026-03-29 | Active |

## Open Questions
- What should back persistent router state: flat files, SQLite, or another storage layer for health state, queues, usage, and evaluation artifacts?
- Which route should be the first production broker surface after `/router/chat`: completions, embeddings, or evaluation submission?
- Should the default overflow behavior for OpenRouter free-tier queue pressure be `wait`, `fallback_to_local`, or `upgrade_to_paid`?
- Should TabbyAPI-backed model switching remain symlink-based at first, or should backend-native load and unload be introduced in the first integration pass?
- When ExLlamaV3 tooling is installed, should EXL3 conversion be exposed through the same job model as EXL2 conversion or a separate workflow while the toolchain is still maturing?
- How should evaluation report scoring be performed initially: human review only, secondary-LLM adjudication, or both?
- What is the minimum documentation package external projects need before they can reliably consume the broker and evaluation APIs?
- How should per-project routing policy overrides be authenticated and authorized?

## Conflicts To Resolve
- GGUF support is described in one place as part of the broader vLLM opportunity, but the backend routing rules still prefer TGW llama.cpp today and direct llama.cpp later. The near-term preferred GGUF backend should be explicitly settled before implementation begins.
- The plan prefers TabbyAPI for the ExLlama lane, but it also notes that EXL3 should not yet be treated as the best path for LoRA-heavy workflows because ExLlamaV3 LoRA support is still a caution area. The boundary between inference-only EXL3 support and LoRA-capable operational paths should be made explicit in the first design pass.
