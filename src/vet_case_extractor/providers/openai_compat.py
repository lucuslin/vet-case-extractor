"""OpenAI-compatible HTTP `/chat/completions` provider using httpx."""

from __future__ import annotations

import json
import os
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel
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

T = TypeVar("T", bound=BaseModel)

_RETRYABLE_EXCEPTIONS = (
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderSchemaError,
    TransientProviderError,
)


class OpenAICompatProvider(LLMProvider):
    """Structured JSON extractor posting to an OpenAI-compatible `/chat/completions` endpoint."""

    provider_name = "openai_compat"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        timeout_seconds: float = 90.0,
        max_retries: int = 3,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        retry_wait_min: float = 0.2,
        retry_wait_max: float = 4.0,
    ) -> None:
        resolved_key = (
            api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        ).strip()
        if not resolved_key:
            raise ProviderConfigError(
                "OPENAI_API_KEY is missing. Configure OPENAI_API_KEY in `.env` or environment variables."
            )

        resolved_base = (
            base_url
            if base_url is not None
            else os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        ).strip()
        resolved_model = (
            model or os.environ.get("VETCASE_OPENAI_MODEL") or "gpt-4o-mini"
        ).strip()

        super().__init__(model_name=resolved_model)
        self.api_key = resolved_key
        self.base_url = resolved_base.rstrip("/")
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, int(max_retries))
        self.retry_wait_min = retry_wait_min
        self.retry_wait_max = retry_wait_max
        self._client = client
        self._transport = transport

    def _endpoint_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _post_completion(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = self._endpoint_url()
        if self._client is not None:
            return self._client.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
            return client.post(url, headers=headers, json=payload)

    def _call_once(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> LLMResponse[T]:
        schema_json = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        augmented_system = (
            f"{system_prompt}\n\n"
            f"请严格输出合法 JSON（不要包含解释或额外文本），并遵守以下 JSON Schema：\n{schema_json}"
        )
        payload = {
            "model": self.model_name,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": augmented_system},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        try:
            response = self._post_completion(payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"OpenAI-compatible request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(f"OpenAI-compatible network error: {exc}") from exc

        status_code = response.status_code
        if status_code in (401, 403):
            raise ProviderConfigError(
                f"OpenAI-compatible authentication error (HTTP {status_code}): check OPENAI_API_KEY."
            )
        if status_code == 429:
            raise ProviderRateLimitError(
                f"OpenAI-compatible rate limit exceeded (HTTP 429): {response.text[:200]}"
            )
        if status_code in (408, 504):
            raise ProviderTimeoutError(
                f"OpenAI-compatible request timed out (HTTP {status_code}): {response.text[:200]}"
            )
        if 500 <= status_code < 600:
            raise TransientProviderError(
                f"OpenAI-compatible server error (HTTP {status_code}): {response.text[:200]}"
            )
        if status_code >= 400:
            raise ProviderError(
                f"OpenAI-compatible API error (HTTP {status_code}): {response.text[:200]}"
            )

        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderSchemaError(
                f"OpenAI-compatible endpoint returned non-JSON HTTP body: {exc}"
            ) from exc

        try:
            choices = body["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderSchemaError(
                f"Missing choices[0].message.content in OpenAI-compatible response: {exc}"
            ) from exc

        if not isinstance(content, str):
            raise ProviderSchemaError(
                f"Expected string message content, got {type(content).__name__}."
            )

        parsed = parse_structured_json(content, response_model)
        usage_raw = body.get("usage") if isinstance(body, dict) else {}
        if not isinstance(usage_raw, dict):
            usage_raw = {}
        prompt_tokens = int(usage_raw.get("prompt_tokens") or 0)
        completion_tokens = int(usage_raw.get("completion_tokens") or 0)
        total_tokens = int(usage_raw.get("total_tokens") or (prompt_tokens + completion_tokens))
        model_used = str(body.get("model") or self.model_name)

        return LLMResponse(
            parsed=parsed,
            model_name=model_used,
            token_usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            raw_text=content,
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
