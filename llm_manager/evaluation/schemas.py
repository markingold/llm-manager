from typing import Any, Literal

from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    case_id: str
    prompt: str
    description: str | None = None
    system: str | None = None
    expected_contains: list[str] = Field(default_factory=list)
    expected_exact: str | None = None
    expected_tool_name: str | None = None
    response_json_schema: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    rubric: str | None = None
    scoring_plugins: list[dict[str, Any]] = Field(default_factory=list)
    min_plugin_score_pct: float | None = None
    max_latency_ms: float | None = None
    max_estimated_cost_usd: float | None = None
    tags: list[str] = Field(default_factory=list)


class EvalVariant(BaseModel):
    variant_id: str
    system: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] = Field(default_factory=list)
    seed: int | None = None
    reasoning_effort: str | None = None


class EvalJudgeConfig(BaseModel):
    enabled: bool = False
    preliminary_model: str = "openrouter.paid:openai/gpt-5.6-luna"
    preliminary_fallback_models: list[str] = Field(default_factory=lambda: [
        "openrouter.paid:deepseek/deepseek-v4-flash",
        "openrouter.paid:google/gemini-2.5-flash-lite",
        "openrouter.paid:minimax/minimax-m3",
    ])
    primary_model: str = "openrouter.paid:openai/gpt-5.6-terra"
    primary_fallback_models: list[str] = Field(default_factory=lambda: [
        "openrouter.paid:anthropic/claude-sonnet-5",
        "openrouter.paid:qwen/qwen3.7-plus",
    ])
    tie_break_model: str = "openrouter.paid:openai/gpt-5.6-sol"
    tie_break_fallback_models: list[str] = Field(default_factory=lambda: [
        "openrouter.paid:anthropic/claude-sonnet-5",
    ])
    preliminary_top_fraction: float = Field(default=0.5, ge=0.1, le=1.0)
    disagreement_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
    minimum_score: float = Field(default=0.7, ge=0.0, le=1.0)
    max_estimated_cost_usd: float = Field(default=5.0, gt=0.0)
    rubric_version: str = "general-v1"
    reasoning_effort: str = "medium"


class LocalEvalRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    suite_name: str
    suite_version: str = "1"
    target_mode: Literal["chat", "intent", "small"] = "chat"
    candidate_models: list[str] = Field(default_factory=list)
    variants: list[EvalVariant] = Field(default_factory=list)
    cases: list[EvalCase] = Field(default_factory=list)
    case_pass_threshold_pct: float | None = None
    suite_pass_threshold_pct: float | None = None
    repetitions: int = Field(default=1, ge=1, le=5)
    require_curated_remote: bool = True
    max_estimated_cost_usd: float = Field(default=10.0, gt=0.0)
    judge: EvalJudgeConfig = Field(default_factory=EvalJudgeConfig)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LocalEvalResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    run_id: str
    suite_name: str
    suite_version: str
    target_mode: str
    created_ts: str
    model_count: int
    variant_count: int
    case_count: int
    result_count: int
    summary: dict[str, Any]
    recommendations: list[str]


class LocalEvalEnqueueResponse(BaseModel):
    run_id: str
    status: str
    priority: Literal["interactive", "batch", "evaluation"]
    queue_position: int
    enqueued_ts: str


class EvalSuitePayload(BaseModel):
    model_config = {"protected_namespaces": ()}
    suite_name: str
    suite_version: str = "1"
    target_mode: Literal["chat", "intent", "small"] = "chat"
    candidate_models: list[str] = Field(default_factory=list)
    variants: list[EvalVariant] = Field(default_factory=list)
    cases: list[EvalCase] = Field(default_factory=list)
    case_pass_threshold_pct: float | None = None
    suite_pass_threshold_pct: float | None = None
    repetitions: int = Field(default=1, ge=1, le=5)
    require_curated_remote: bool = True
    max_estimated_cost_usd: float = Field(default=10.0, gt=0.0)
    judge: EvalJudgeConfig = Field(default_factory=EvalJudgeConfig)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalRerunRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    async_run: bool = False
    priority: Literal["interactive", "batch", "evaluation"] = "evaluation"
    candidate_models: list[str] = Field(default_factory=list)
    variants: list[EvalVariant] = Field(default_factory=list)
    case_pass_threshold_pct: float | None = None
    suite_pass_threshold_pct: float | None = None
    repetitions: int | None = Field(default=None, ge=1, le=5)
    require_curated_remote: bool | None = None
    max_estimated_cost_usd: float | None = Field(default=None, gt=0.0)
    judge: EvalJudgeConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
