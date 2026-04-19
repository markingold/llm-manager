from abc import ABC, abstractmethod
from typing import Any


class ProviderAdapter(ABC):
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def chat(self, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def completions(self, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def embeddings(self, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def normalize_error(self, error: Exception) -> dict[str, Any]:
        return {
            "type": "provider_error",
            "message": str(error),
            "retryable": False,
        }
