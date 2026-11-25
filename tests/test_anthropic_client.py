"""Claude provider: request shape, parsing, refusals and error mapping, without network calls."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from quant_signal.application.llm_analysis import (
    ANTHROPIC_FALLBACK_BETA,
    AnthropicClient,
    LLMProviderError,
    OpenAICompatibleClient,
    create_judge_llm_client,
    create_llm_client,
    llm_model_name,
)
from quant_signal.settings import Settings


def _message(text: str, *, stop_reason: str = "end_turn", category: str | None = None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if category else None,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
    )


class _FakeStream:
    def __init__(self, message, chunks: list[str]) -> None:
        self._message = message
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    @property
    def text_stream(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk

        return gen()

    async def get_final_message(self):
        return self._message


class _FakeAnthropic:
    """Stands in for anthropic.AsyncAnthropic: records requests, replays one message."""

    def __init__(self, message=None, *, chunks: list[str] | None = None, error=None) -> None:
        self.requests: list[dict] = []
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.requests.append(kwargs)
                if error is not None:
                    raise error
                return _FakeStream(message, chunks or [])

        self.beta = SimpleNamespace(messages=_Messages())


def _client(fake: _FakeAnthropic, **overrides) -> AnthropicClient:
    options = {
        "api_key": "unused",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "max_tokens": 16000,
        "timeout_seconds": 600,
        "client": fake,
    }
    options.update(overrides)
    return AnthropicClient(**options)


async def test_complete_json_sends_a_current_claude_request_and_parses_the_answer() -> None:
    fake = _FakeAnthropic(_message('```json\n{"headline": "偏多", "stance": "bullish"}\n```'))

    result = await _client(fake, effort="high").complete_json("系統提示", {"symbol": "2330.TW"})

    assert result == {"headline": "偏多", "stance": "bullish"}
    request = fake.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["system"] == "系統提示"
    assert request["messages"] == [{"role": "user", "content": '{"symbol": "2330.TW"}'}]
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"] == {"effort": "high"}
    assert request["max_tokens"] == 16000
    assert request["betas"] == [ANTHROPIC_FALLBACK_BETA]
    assert request["fallbacks"] == "default"
    # Current Claude models reject sampling parameters.
    assert not {"temperature", "top_p", "top_k"} & request.keys()


async def test_stream_text_yields_text_chunks() -> None:
    fake = _FakeAnthropic(_message("全文"), chunks=["第一段", "", "第二段"])

    chunks = [chunk async for chunk in _client(fake).stream_text("系統", {"a": 1})]

    assert chunks == ["第一段", "第二段"]


async def test_a_refusal_becomes_a_provider_error_with_its_category() -> None:
    fake = _FakeAnthropic(_message("", stop_reason="refusal", category="cyber"))

    with pytest.raises(LLMProviderError, match="cyber"):
        await _client(fake).complete_json("系統", {})


async def test_a_truncated_answer_says_which_setting_to_raise() -> None:
    fake = _FakeAnthropic(_message('{"headline": ', stop_reason="max_tokens"))

    with pytest.raises(LLMProviderError, match="ANTHROPIC_MAX_TOKENS"):
        await _client(fake).complete_json("系統", {})


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    return cls("boom", response=response, body=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_status_error(anthropic.AuthenticationError, 401), "ANTHROPIC_API_KEY"),
        (_status_error(anthropic.RateLimitError, 429), "速率"),
        (_status_error(anthropic.InternalServerError, 500), "HTTP 500"),
    ],
)
async def test_sdk_errors_map_to_value_errors_the_routes_already_handle(error, expected) -> None:
    fake = _FakeAnthropic(error=error)

    with pytest.raises(LLMProviderError, match=expected) as caught:
        await _client(fake).complete_json("系統", {})

    assert isinstance(caught.value, ValueError)  # REST routes map ValueError to 502


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_anthropic_provider_needs_only_an_api_key() -> None:
    missing_key = _settings(llm_enabled=True, llm_provider="anthropic")
    assert missing_key.llm_errors() == ["ANTHROPIC_API_KEY"]

    ready = _settings(llm_enabled=True, llm_provider="anthropic", anthropic_api_key="k")
    assert ready.llm_errors() == []
    client = create_llm_client(ready)
    assert isinstance(client, AnthropicClient)
    assert client.model_name == "claude-opus-5-5"
    assert llm_model_name(ready) == "claude-opus-5-5"


def test_openai_compatible_provider_is_unchanged() -> None:
    settings = _settings(llm_enabled=True, llm_model="qwen3:8b")
    assert isinstance(create_llm_client(settings), OpenAICompatibleClient)
    assert llm_model_name(settings) == "qwen3:8b"


def test_judge_can_use_a_different_claude_model() -> None:
    settings = _settings(
        judge_llm_enabled=True,
        judge_llm_provider="anthropic",
        anthropic_api_key="k",
        judge_anthropic_model="claude-fable-5-2",
    )
    judge = create_judge_llm_client(settings)
    assert isinstance(judge, AnthropicClient)
    assert judge.model_name == "claude-fable-5-2"

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        create_judge_llm_client(
            _settings(judge_llm_enabled=True, judge_llm_provider="anthropic")
        )
