from typing import Any, Literal

from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    case_id: str
    prompt: str
    system: str | None = None
    expected_contains: list[str] = Field(default_factory=list)
    scoring_plugins: list[dict[str, Any]] = Field(default_factory=list)
    min_plugin_score_pct: float | None = None
    tags: list[str] = Field(default_factory=list)


class EvalVariant(BaseModel):
    variant_id: str
    system: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] = Field(default_factory=list)


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
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalRerunRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    async_run: bool = False
    priority: Literal["interactive", "batch", "evaluation"] = "evaluation"
    candidate_models: list[str] = Field(default_factory=list)
    variants: list[EvalVariant] = Field(default_factory=list)
    case_pass_threshold_pct: float | None = None
    suite_pass_threshold_pct: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
