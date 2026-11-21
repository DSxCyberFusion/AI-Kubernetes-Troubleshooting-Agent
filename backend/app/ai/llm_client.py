import time
from typing import Sequence

import httpx
from loguru import logger

from app.ai.errors import LLMClientError
from app.core.config import get_settings


class OpenAICompatibleClient:
    DEFAULT_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    MAX_ATTEMPTS = 3
    RETRY_BASE_SECONDS = 1.0
    MAX_RETRY_DELAY_SECONDS = 8.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        api_url: str | None = None,
    ) -> None:
        settings = get_settings()
        custom_provider_configured = any(
            (
                settings.ai_api_url.strip(),
                settings.ai_api_key.strip(),
                settings.ai_model.strip(),
            )
        )
        configured_api_key = (
            settings.ai_api_key
            if custom_provider_configured
            else settings.openrouter_api_key
        )
        configured_model = (
            settings.ai_model if custom_provider_configured else settings.openrouter_model
        )
        configured_api_url = (
            settings.ai_api_url if custom_provider_configured else self.DEFAULT_API_URL
        )
        self.api_key = (configured_api_key if api_key is None else api_key).strip()
        self.model = (configured_model if model is None else model).strip()
        self.api_url = (configured_api_url if api_url is None else api_url).strip()
        self.timeout_seconds = (
            settings.ai_timeout_seconds if timeout_seconds is None else timeout_seconds
        )

    def complete(self, messages: Sequence[dict[str, str]]) -> str:
        if not self.api_url or not self.api_key or not self.model:
            raise LLMClientError(
                "AI provider configuration is incomplete. Set AI_API_URL, AI_API_KEY, and AI_MODEL together, or use OPENROUTER_API_KEY and OPENROUTER_MODEL."
            )

        request_body = {
            "model": self.model,
            "messages": list(messages),
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                response = httpx.post(
                    self.api_url,
                    headers=headers,
                    json=request_body,
                    timeout=self.timeout_seconds,
                )
            except httpx.TransportError as exc:
                logger.warning(
                    "AI provider transport failure ({}) on attempt {}/{}",
                    type(exc).__name__,
                    attempt,
                    self.MAX_ATTEMPTS,
                )
                if attempt == self.MAX_ATTEMPTS:
                    raise LLMClientError(
                        "The AI provider could not be reached. Check the connection and try again."
                    ) from exc
                self._wait_before_retry(attempt)
                continue

            if response.status_code >= 400:
                retryable = response.status_code == 429 or response.status_code >= 500
                logger.warning(
                    "AI provider returned HTTP {} on attempt {}/{}",
                    response.status_code,
                    attempt,
                    self.MAX_ATTEMPTS,
                )
                if retryable and attempt < self.MAX_ATTEMPTS:
                    self._wait_before_retry(
                        attempt,
                        response.headers.get("Retry-After"),
                    )
                    continue
                if response.status_code in {401, 403}:
                    raise LLMClientError(
                        "AI diagnosis was rejected. Verify the configured provider API key and model access."
                    )
                if response.status_code == 402:
                    raise LLMClientError(
                        "AI diagnosis is unavailable because the provider plan or credit balance does not allow this request. Check the provider account and model access."
                    )
                if response.status_code == 429:
                    raise LLMClientError(
                        "AI diagnosis is temporarily rate-limited. Wait a moment and try again."
                    )
                if response.status_code >= 500:
                    raise LLMClientError(
                        "The AI provider is temporarily unavailable. Try again shortly."
                    )
                raise LLMClientError(
                    f"AI provider request failed with HTTP {response.status_code}."
                )

            return self._extract_content(response)

        raise LLMClientError("AI provider request failed after multiple attempts.")

    @classmethod
    def _wait_before_retry(
        cls,
        attempt: int,
        retry_after: str | None = None,
    ) -> None:
        delay = min(
            cls.RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
            cls.MAX_RETRY_DELAY_SECONDS,
        )
        if retry_after is not None:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        time.sleep(min(delay, cls.MAX_RETRY_DELAY_SECONDS))

    @staticmethod
    def _extract_content(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMClientError("AI provider returned invalid JSON.") from exc

        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            raise LLMClientError("AI provider response did not contain a completion.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise LLMClientError(
                "The selected AI model did not return text. Choose a text-generation chat model for Kubernetes diagnosis."
            )
        return content.strip()
