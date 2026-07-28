# Logging

The LLM Manager API emits v3-compatible JSONL to stderr for journald through
`llm_manager.logging_setup`. It bridges Python/framework logs, accepts and
echoes request IDs, emits typed API errors, and reports startup configuration.

Managed inference engines remain third-party processes whose framework text is
captured from their exact `llm-*.service` journals. The repository launcher
emits structured preflight/start events; Banana Monitor treats subsequent
backend output with a framework parser.

Use `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR|CRITICAL` and `LOG_FORMAT=json`.
Never log prompts, completions, API credentials, authorization headers, or
provider response bodies.
