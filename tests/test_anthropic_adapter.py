"""Anthropic adapter against a mocked HTTP transport: no network, no API spend."""

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from personal_ai_assistant.adapters.anthropic_model import (
    DEFAULT_MODEL,
    AnthropicConfigError,
    AnthropicSettings,
    AnthropicStructuredModel,
)
from personal_ai_assistant.planning.contracts import ModelUnavailableError, StructuredRequest
from personal_ai_assistant.planning.llm_planner import plan_output_schema

KEY = "sk-ant-api03-synthetic-adapter-key-000000000"  # synthetic  gitleaks:allow
REQUEST = StructuredRequest(
    system="SYSTEM PROMPT", user="明天下午有什麼行程？", output_schema=plan_output_schema(["a.b"])
)
PLAN_TEXT = '{"summary": "s", "actions": [], "questions": []}'


def message(
    content: list[dict[str, Any]] | None = None, stop_reason: str = "end_turn"
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL,
        "content": content if content is not None else [{"type": "text", "text": PLAN_TEXT}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


class Recorder:
    def __init__(self, respond: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.respond(request)


def model_for(
    respond: Callable[[httpx2.Request], httpx2.Response],
) -> tuple[AnthropicStructuredModel, Recorder]:
    recorder = Recorder(respond)
    client = anthropic.Anthropic(
        api_key=KEY,
        base_url="http://anthropic.test",
        http_client=httpx2.Client(transport=httpx2.MockTransport(recorder)),
        max_retries=0,
    )
    return AnthropicStructuredModel(client, model=DEFAULT_MODEL, effort="medium"), recorder


def test_request_shape_and_raw_text_returned() -> None:
    model, recorder = model_for(lambda _: httpx2.Response(200, json=message()))
    assert model.generate(REQUEST) == PLAN_TEXT
    (sent,) = recorder.requests
    assert sent.url.path == "/v1/messages"
    assert sent.headers["x-api-key"] == KEY
    body = json.loads(sent.content)
    assert body["model"] == DEFAULT_MODEL
    assert body["system"] == "SYSTEM PROMPT"
    assert body["messages"] == [{"role": "user", "content": "明天下午有什麼行程？"}]
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"] == {
        "type": "json_schema",
        "schema": plan_output_schema(["a.b"]),
    }
    assert "tool_choice" not in body  # forced tool use is rejected by current models
    assert KEY not in sent.content.decode()


def test_thinking_blocks_are_ignored() -> None:
    content = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": PLAN_TEXT},
    ]
    model, _ = model_for(lambda _: httpx2.Response(200, json=message(content)))
    assert model.generate(REQUEST) == PLAN_TEXT


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (message(stop_reason="refusal"), "declined"),
        (message(stop_reason="max_tokens"), "stopped early"),
        (message(content=[]), "no single structured answer"),
        (
            message(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]),
            "no single structured answer",
        ),
    ],
)
def test_unusable_responses_raise(payload: dict[str, Any], match: str) -> None:
    model, _ = model_for(lambda _: httpx2.Response(200, json=payload))
    with pytest.raises(ModelUnavailableError, match=match):
        model.generate(REQUEST)


def _error(status: int) -> Callable[[httpx2.Request], httpx2.Response]:
    body = {"type": "error", "error": {"type": "x", "message": f"bad key {KEY}"}}
    return lambda _: httpx2.Response(status, json=body)


def _disconnect(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError(f"cannot reach {KEY}", request=request)


def _timeout(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("slow", request=request)


@pytest.mark.parametrize(
    ("respond", "match"),
    [
        (_error(401), "rejected the credentials"),
        (_error(429), "rate limit"),
        (_error(500), "HTTP 500"),
        (_error(400), "HTTP 400"),
        (_disconnect, "unreachable"),
        (_timeout, "timed out"),
    ],
)
def test_sdk_errors_become_neutral_errors_without_secrets(
    respond: Callable[[httpx2.Request], httpx2.Response], match: str
) -> None:
    model, _ = model_for(respond)
    with pytest.raises(ModelUnavailableError, match=match) as info:
        model.generate(REQUEST)
    assert KEY not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert not isinstance(info.value, anthropic.AnthropicError)


def test_settings_from_env() -> None:
    settings = AnthropicSettings.from_env(
        {
            "ANTHROPIC_API_KEY": f"  {KEY} ",
            "ASSISTANT_ANTHROPIC_MODEL": "claude-sonnet-5-5",
            "ASSISTANT_ANTHROPIC_TIMEOUT_SECONDS": "30",
            "ASSISTANT_ANTHROPIC_EFFORT": "low",
        }
    )
    assert settings.api_key.get_secret_value() == KEY
    assert settings.model == "claude-sonnet-5-5"
    assert settings.timeout_seconds == 30
    assert settings.effort == "low"
    assert KEY not in repr(settings)
    assert KEY not in str(settings.model_dump())
    defaults = AnthropicSettings.from_env({"ANTHROPIC_API_KEY": KEY})
    assert defaults.model == DEFAULT_MODEL


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"ANTHROPIC_API_KEY": "   "},
        {"ANTHROPIC_API_KEY": KEY, "ASSISTANT_ANTHROPIC_EFFORT": "extreme"},
        {"ANTHROPIC_API_KEY": KEY, "ASSISTANT_ANTHROPIC_MODEL": "bad model\n"},
        {"ANTHROPIC_API_KEY": KEY, "ASSISTANT_ANTHROPIC_TIMEOUT_SECONDS": "0"},
    ],
)
def test_bad_settings_raise_without_key(env: dict[str, str]) -> None:
    with pytest.raises(AnthropicConfigError) as info:
        AnthropicSettings.from_env(env)
    assert KEY not in str(info.value)


def test_from_settings_builds_client_without_network() -> None:
    model = AnthropicStructuredModel.from_settings(
        AnthropicSettings.from_env({"ANTHROPIC_API_KEY": KEY})
    )
    assert KEY not in repr(model)
