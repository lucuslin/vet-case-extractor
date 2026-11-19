"""Base provider abstractions, typed exceptions, and structured response models."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)


class ProviderError(RuntimeError):
    """Base error for all LLM provider and import failures."""


class ProviderConfigError(ProviderError):
    """Raised when provider configuration or credentials are missing or invalid."""


class ProviderRateLimitError(ProviderError):
    """Raised when the upstream provider rejects a call due to rate limits or quota."""


class ProviderTimeoutError(ProviderError):
    """Raised when an upstream provider call times out."""


class ProviderSchemaError(ProviderError):
    """Raised when provider output or imported JSON fails strict JSON/Pydantic validation."""


class TransientProviderError(ProviderError):
    """Retryable upstream server/network error (e.g. HTTP 500/502/503)."""


@dataclass
class LLMResponse(Generic[T]):
    """Structured response container returned by an LLMProvider."""

    parsed: T
    model_name: str
    token_usage: dict[str, int] = field(default_factory=dict)
    raw_text: str = ""


def strip_json_code_fences(raw_text: str) -> str:
    """Strip complete outer markdown ```json ... ``` fences without masking truncated JSON."""
    stripped = raw_text.strip()
    match = _CODE_FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def parse_structured_json(raw_text: str, response_model: type[T]) -> T:
    """Strictly parse and validate raw JSON text against a Pydantic response model."""
    cleaned = strip_json_code_fences(raw_text)
    if not cleaned:
        raise ProviderSchemaError("Provider returned empty text where JSON was expected.")
    try:
        json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ProviderSchemaError(f"Malformed or truncated JSON response: {exc}") from exc

    try:
        return response_model.model_validate_json(cleaned)
    except ValidationError as exc:
        raise ProviderSchemaError(
            f"JSON response failed {response_model.__name__} validation: {exc}"
        ) from exc


class LLMProvider(ABC):
    """Abstract interface for structured JSON extraction via an LLM API."""

    provider_name: str = "base"

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    @abstractmethod
    def extract_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> LLMResponse[T]:
        """Run structured generation returning a validated Pydantic model instance."""
