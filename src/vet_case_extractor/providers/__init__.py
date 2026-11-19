"""LLM provider factory and exports."""

from __future__ import annotations

from vet_case_extractor.config import AppConfig
from vet_case_extractor.providers.agent_bridge import (
    export_agent_batch,
    parse_agent_import_file,
)
from vet_case_extractor.providers.base import (
    LLMProvider,
    LLMResponse,
    ProviderConfigError,
    ProviderError,
    ProviderRateLimitError,
    ProviderSchemaError,
    ProviderTimeoutError,
)
from vet_case_extractor.providers.gemini import GeminiProvider
from vet_case_extractor.providers.openai_compat import OpenAICompatProvider


def get_provider(name: str, cfg: AppConfig, model: str | None = None) -> LLMProvider:
    """Instantiate a configured LLMProvider by name (`gemini` or `openai_compat`)."""
    normalized = (name or "").strip().lower()
    if normalized == "gemini":
        return GeminiProvider(
            model=model or cfg.llm.gemini_model,
            temperature=cfg.llm.temperature,
            timeout_seconds=cfg.llm.timeout_seconds,
            max_retries=cfg.llm.max_retries,
        )
    if normalized in ("openai_compat", "openai-compat", "openai"):
        return OpenAICompatProvider(
            model=model or cfg.llm.openai_model,
            temperature=cfg.llm.temperature,
            timeout_seconds=cfg.llm.timeout_seconds,
            max_retries=cfg.llm.max_retries,
        )
    raise ProviderConfigError(
        f"Unsupported LLM provider '{name}'. Supported providers: gemini, openai_compat."
    )


__all__ = [
    "GeminiProvider",
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatProvider",
    "ProviderConfigError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderSchemaError",
    "ProviderTimeoutError",
    "export_agent_batch",
    "get_provider",
    "parse_agent_import_file",
]
