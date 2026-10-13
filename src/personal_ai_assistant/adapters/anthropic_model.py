"""Anthropic Claude adapter for the vendor-neutral ``StructuredModel`` port.

This is the only module allowed to import the ``anthropic`` SDK (enforced by
ruff ``banned-api`` and an architecture test). It maps a ``StructuredRequest``
to one Messages API call with a JSON-schema output format and returns the raw
JSON text; validation happens in ``planning.llm_planner``. Every SDK failure is
translated to ``ModelUnavailableError`` with a fixed, credential-free message,
and exception chaining is suppressed so SDK objects (requests, headers) never
reach callers, logs or the audit trail.
"""

from collections.abc import Mapping
from typing import Literal, Self

import anthropic
from anthropic.types import TextBlock
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from personal_ai_assistant.planning.contracts import (
    ModelUnavailableError,
    StructuredRequest,
)

DEFAULT_MODEL = "claude-opus-5-5"
API_KEY_ENV = "ANTHROPIC_API_KEY"

type Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AnthropicConfigError(Exception):
    """Adapter configuration is missing or invalid. Message never contains the key."""


class AnthropicSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr
    model: str = Field(default=DEFAULT_MODEL, min_length=1, pattern=r"^[A-Za-z0-9._:@-]+$")
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_tokens: int = Field(default=16_000, ge=1_024, le=32_000)
    effort: Effort = "medium"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        """Read ``ANTHROPIC_API_KEY`` and optional ``ASSISTANT_ANTHROPIC_*`` overrides."""
        key = env.get(API_KEY_ENV, "").strip()
        if not key:
            raise AnthropicConfigError(f"{API_KEY_ENV} is not set")
        values: dict[str, object] = {"api_key": key}
        for field, name in (
            ("model", "MODEL"),
            ("timeout_seconds", "TIMEOUT_SECONDS"),
            ("effort", "EFFORT"),
        ):
            raw = env.get(f"ASSISTANT_ANTHROPIC_{name}", "").strip()
            if raw:
                values[field] = raw
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            fields = sorted({str(err["loc"][0]) for err in exc.errors() if err["loc"]})
            raise AnthropicConfigError(f"invalid Anthropic settings: {fields}") from None


class AnthropicStructuredModel:
    """``StructuredModel`` backed by the Anthropic Messages API (structured outputs)."""

    def __init__(
        self,
        client: anthropic.Anthropic,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 16_000,
        effort: Effort = "medium",
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._effort: Effort = effort

    @classmethod
    def from_settings(cls, settings: AnthropicSettings) -> Self:
        client = anthropic.Anthropic(
            api_key=settings.api_key.get_secret_value(),
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,  # SDK backs off on 408/409/429/5xx
        )
        return cls(
            client, model=settings.model, max_tokens=settings.max_tokens, effort=settings.effort
        )

    def __repr__(self) -> str:
        return f"AnthropicStructuredModel(model={self._model!r})"

    def generate(self, request: StructuredRequest) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                output_config={
                    "effort": self._effort,
                    "format": {"type": "json_schema", "schema": dict(request.output_schema)},
                },
            )
        except anthropic.APITimeoutError:
            raise ModelUnavailableError("model request timed out") from None
        except anthropic.APIConnectionError:
            raise ModelUnavailableError("model service unreachable") from None
        except anthropic.AuthenticationError:
            raise ModelUnavailableError("model service rejected the credentials") from None
        except anthropic.RateLimitError:
            raise ModelUnavailableError("model service rate limit reached") from None
        except anthropic.APIStatusError as exc:
            raise ModelUnavailableError(f"model service error (HTTP {exc.status_code})") from None
        except anthropic.AnthropicError:
            raise ModelUnavailableError("model service error") from None

        if response.stop_reason == "refusal":
            raise ModelUnavailableError("model declined the request")
        if response.stop_reason != "end_turn":
            # max_tokens / context exhaustion etc.: the JSON may be cut off.
            raise ModelUnavailableError(f"model stopped early ({response.stop_reason})")
        texts = [block.text for block in response.content if isinstance(block, TextBlock)]
        if len(texts) != 1:
            raise ModelUnavailableError("model returned no single structured answer")
        return texts[0]
