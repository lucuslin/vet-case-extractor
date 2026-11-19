"""Official Google Gemini provider built on the `google-genai` SDK."""

from __future__ import annotations

import os
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from vet_case_extractor.providers.base import (
    LLMProvider,
    LLMResponse,
    ProviderConfigError,
    ProviderError,
    ProviderRateLimitError,
    ProviderSchemaError,
    ProviderTimeoutError,
    TransientProviderError,
    parse_structured_json,
)

try:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types
except ImportError:  # pragma: no cover
    genai = None  # type: ignore[assignment]
    genai_errors = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

T = TypeVar("T", bound=BaseModel)

_RETRYABLE_EXCEPTIONS = (
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderSchemaError,
    TransientProviderError,
)


def _extract_usage(response: Any) -> dict[str, int]:
    """Extract normalized token counts from Gemini response metadata."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt_tokens = int(getattr(meta, "prompt_token_count", 0) or 0)
    completion_tokens = int(getattr(meta, "candidates_token_count", 0) or 0)
    total_tokens = int(getattr(meta, "total_token_count", 0) or (prompt_tokens + completion_tokens))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _classify_gemini_exception(exc: Exception) -> ProviderError:
    """Map google-genai / transport exceptions onto typed ProviderError subclasses."""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return ProviderTimeoutError(f"Gemini request timed out: {exc}")

    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "").upper()
    msg = str(exc)
    msg_lower = msg.lower()

    if code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        return ProviderConfigError(
            f"Gemini authentication/permission error (code={code}): check GEMINI_API_KEY."
        )
    if (
        code == 429
        or status == "RESOURCE_EXHAUSTED"
        or "rate limit" in msg_lower
        or "quota" in msg_lower
        or "429" in msg_lower
    ):
        return ProviderRateLimitError(f"Gemini rate limit or quota exceeded: {msg}")
    if (
        code in (408, 504)
        or status == "DEADLINE_EXCEEDED"
        or "timeout" in msg_lower
        or "timed out" in msg_lower
    ):
        return ProviderTimeoutError(f"Gemini request timed out: {msg}")
    if isinstance(code, int) and 500 <= code < 600:
        return TransientProviderError(f"Gemini transient server error (code={code}): {msg}")
    if genai_errors is not None and isinstance(exc, genai_errors.ServerError):
        return TransientProviderError(f"Gemini server error: {msg}")
    return ProviderError(f"Gemini API error: {msg}")


class GeminiProvider(LLMProvider):
    """Structured extraction provider using official `google.genai` SDK."""

    provider_name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        timeout_seconds: float = 90.0,
        max_retries: int = 3,
        client: Any | None = None,
        retry_wait_min: float = 0.2,
        retry_wait_max: float = 4.0,
    ) -> None:
        resolved_model = (
            model or os.environ.get("VETCASE_GEMINI_MODEL") or "gemini-2.5-flash"
        ).strip()
        super().__init__(model_name=resolved_model)
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, int(max_retries))
        self.retry_wait_min = retry_wait_min
        self.retry_wait_max = retry_wait_max

        if client is not None:
            self.client = client
            return

        if genai is None or types is None:
            raise ProviderConfigError(
                "google-genai is not installed. Install it via `pip install google-genai`."
            )

        resolved_key = (api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")).strip()
        if not resolved_key:
            raise ProviderConfigError(
                "GEMINI_API_KEY is missing. Configure GEMINI_API_KEY in `.env` or environment variables."
            )

        timeout_ms = max(1000, int(self.timeout_seconds * 1000))
        self.client = genai.Client(
            api_key=resolved_key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )

    def _call_once(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> LLMResponse[T]:
        if types is None:  # pragma: no cover
            raise ProviderConfigError("google-genai types module unavailable.")

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self.temperature,
            response_mime_type="application/json",
            response_schema=response_model,
        )

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=user_prompt,
                config=config,
            )
        except Exception as exc:
            raise _classify_gemini_exception(exc) from exc

        raw_text = getattr(response, "text", None) or ""
        parsed_obj = getattr(response, "parsed", None)

        if isinstance(parsed_obj, response_model):
            parsed = parsed_obj
            if not raw_text:
                raw_text = parsed.model_dump_json()
        elif isinstance(parsed_obj, dict):
            try:
                parsed = response_model.model_validate(parsed_obj)
            except ValidationError as exc:
                raise ProviderSchemaError(
                    f"Gemini parsed payload failed {response_model.__name__} validation: {exc}"
                ) from exc
            if not raw_text:
                raw_text = parsed.model_dump_json()
        elif raw_text:
            parsed = parse_structured_json(raw_text, response_model)
        else:
            raise ProviderSchemaError("Gemini returned neither parsed payload nor JSON text.")

        return LLMResponse(
            parsed=parsed,
            model_name=self.model_name,
            token_usage=_extract_usage(response),
            raw_text=raw_text,
        )

    def extract_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> LLMResponse[T]:
        retrying = Retrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=wait_exponential(
                multiplier=0.5,
                min=self.retry_wait_min,
                max=self.retry_wait_max,
            ),
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            reraise=True,
        )
        return retrying(self._call_once, system_prompt, user_prompt, response_model)
