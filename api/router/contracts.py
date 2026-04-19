from pydantic import BaseModel, Field
from typing import Any, Literal


class RouterProviderPreferences(BaseModel):
    strategy: Literal[
        "default",
        "local_first",
        "free_first",
        "paid_first",
        "best_available",
        "strict_provider",
    ] = "default"
    free_only: bool | None = None
    paid_allowed: bool | None = None
    allow_fallbacks: bool | None = None
    preferred_provider: str | None = None
    preferred_model_tags: list[str] = Field(default_factory=list)


class RouterModelPreferences(BaseModel):
    preferred_model: str | None = None
    preferred_model_tags: list[str] = Field(default_factory=list)


class RouterChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class RouterChatRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    task_type: Literal["chat"] = "chat"
    messages: list[RouterChatMessage] = Field(default_factory=list)
    system: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] = Field(default_factory=list)
    json_schema: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    provider_preferences: RouterProviderPreferences = Field(default_factory=RouterProviderPreferences)
    model_preferences: RouterModelPreferences = Field(default_factory=RouterModelPreferences)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouterChoice(BaseModel):
    index: int = 0
    message: dict[str, Any]
    finish_reason: str = "stop"


class RouterUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class RouterChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    provider: str
    strategy: str
    choices: list[RouterChoice]
    usage: RouterUsage
    routing: dict[str, Any]


class RouterCompletionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    task_type: Literal["completion"] = "completion"
    prompt: str
    system: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] = Field(default_factory=list)
    provider_preferences: RouterProviderPreferences = Field(default_factory=RouterProviderPreferences)
    model_preferences: RouterModelPreferences = Field(default_factory=RouterModelPreferences)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouterCompletionChoice(BaseModel):
    index: int = 0
    text: str = ""
    finish_reason: str = "stop"


class RouterCompletionResponse(BaseModel):
    id: str
    object: str = "text_completion"
    created: int
    model: str
    provider: str
    strategy: str
    choices: list[RouterCompletionChoice]
    usage: RouterUsage
    routing: dict[str, Any]


class RouterEmbedRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    task_type: Literal["embed"] = "embed"
    input: str | list[str]
    provider_preferences: RouterProviderPreferences = Field(default_factory=RouterProviderPreferences)
    model_preferences: RouterModelPreferences = Field(default_factory=RouterModelPreferences)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouterEmbedDatum(BaseModel):
    index: int
    embedding: list[float]


class RouterEmbedResponse(BaseModel):
    id: str
    object: str = "list"
    created: int
    model: str
    provider: str
    strategy: str
    data: list[RouterEmbedDatum]
    usage: RouterUsage
    routing: dict[str, Any]
