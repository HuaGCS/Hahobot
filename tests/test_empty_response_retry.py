"""Tests for bounded empty-LLM-response retry and provider-pool failover."""

import pytest

from hahobot.providers.base import LLMProvider, LLMResponse
from hahobot.providers.pool_provider import ProviderPoolEntry, ProviderPoolProvider


class ScriptedProvider(LLMProvider):
    """Minimal provider that returns scripted responses in order."""

    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def get_default_model(self) -> str:
        return "test-model"


# ---------------------------------------------------------------------------
# Unit test for _is_blank_retryable_response
# ---------------------------------------------------------------------------


class TestIsBlankRetryable:
    def test_blank_stop_is_retryable(self) -> None:
        assert LLMProvider._is_blank_retryable_response(
            LLMResponse(content="", finish_reason="stop")
        )

    def test_blank_stop_with_whitespace_is_retryable(self) -> None:
        assert LLMProvider._is_blank_retryable_response(
            LLMResponse(content="  \n  ", finish_reason="stop")
        )

    def test_blank_none_is_retryable(self) -> None:
        assert LLMProvider._is_blank_retryable_response(
            LLMResponse(content=None, finish_reason="stop")
        )

    def test_error_reason_not_retryable(self) -> None:
        assert not LLMProvider._is_blank_retryable_response(
            LLMResponse(content="", finish_reason="error")
        )

    def test_content_filter_not_retryable(self) -> None:
        assert not LLMProvider._is_blank_retryable_response(
            LLMResponse(content="", finish_reason="content_filter")
        )

    def test_length_not_retryable(self) -> None:
        assert not LLMProvider._is_blank_retryable_response(
            LLMResponse(content="", finish_reason="length")
        )

    def test_tool_calls_not_retryable(self) -> None:
        assert not LLMProvider._is_blank_retryable_response(
            LLMResponse(content="", finish_reason="stop", tool_calls=[None])  # type: ignore[list-item]
        )

    def test_reasoning_content_not_retryable(self) -> None:
        assert not LLMProvider._is_blank_retryable_response(
            LLMResponse(content="", finish_reason="stop", reasoning_content="thinking...")
        )

    def test_nonblank_content_not_retryable(self) -> None:
        assert not LLMProvider._is_blank_retryable_response(
            LLMResponse(content="hello", finish_reason="stop")
        )


# ---------------------------------------------------------------------------
# Retry integration tests
# ---------------------------------------------------------------------------


class TestChatWithRetryBlankRetry:
    async def _monkeypatch_sleep(self, monkeypatch, delays: list) -> None:
        """Replace asyncio.sleep with a fast no-op that records delays."""

        async def _fake_sleep(delay: float) -> None:
            delays.append(delay)

        monkeypatch.setattr("hahobot.providers.base.asyncio.sleep", _fake_sleep)

    @pytest.mark.asyncio
    async def test_retry_blank_then_succeed(self, monkeypatch) -> None:
        """Two blank responses then a real one; succeeds on third call."""
        provider = ScriptedProvider(
            [
                LLMResponse(content="", finish_reason="stop"),
                LLMResponse(content="", finish_reason="stop"),
                LLMResponse(content="hi", finish_reason="stop"),
            ]
        )
        delays: list[float] = []
        await self._monkeypatch_sleep(monkeypatch, delays)

        response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

        assert response.content == "hi"
        assert provider.calls == 3
        assert delays == [1, 2]

    @pytest.mark.asyncio
    async def test_bounded_exhaustion(self, monkeypatch) -> None:
        """Always blank; stops after len(_CHAT_RETRY_DELAYS)+1 = 4 calls."""
        provider = ScriptedProvider(
            [
                LLMResponse(content="", finish_reason="stop"),
                LLMResponse(content="", finish_reason="stop"),
                LLMResponse(content="", finish_reason="stop"),
                LLMResponse(content="", finish_reason="stop"),
            ]
        )
        delays: list[float] = []
        await self._monkeypatch_sleep(monkeypatch, delays)

        response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

        assert response.content == ""
        assert response.finish_reason == "stop"
        assert provider.calls == 4
        assert delays == [1, 2, 4]

    @pytest.mark.asyncio
    async def test_content_filter_guard(self, monkeypatch) -> None:
        """finish_reason='content_filter' is returned immediately, not retried."""
        provider = ScriptedProvider([LLMResponse(content="", finish_reason="content_filter")])
        delays: list[float] = []
        await self._monkeypatch_sleep(monkeypatch, delays)

        response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

        assert response.finish_reason == "content_filter"
        assert provider.calls == 1
        assert delays == []

    @pytest.mark.asyncio
    async def test_length_guard(self, monkeypatch) -> None:
        """finish_reason='length' is returned immediately, not retried."""
        provider = ScriptedProvider([LLMResponse(content="", finish_reason="length")])
        delays: list[float] = []
        await self._monkeypatch_sleep(monkeypatch, delays)

        response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

        assert response.finish_reason == "length"
        assert provider.calls == 1
        assert delays == []

    @pytest.mark.asyncio
    async def test_tool_calls_guard(self, monkeypatch) -> None:
        """Empty content but non-empty tool_calls is returned immediately."""
        provider = ScriptedProvider(
            [
                LLMResponse(
                    content="",
                    finish_reason="stop",
                    tool_calls=[
                        # minimal ToolCallRequest
                        type("ToolCallRequest", (), {"id": "1", "name": "f", "arguments": {}})()
                    ],
                )
            ]
        )
        delays: list[float] = []
        await self._monkeypatch_sleep(monkeypatch, delays)

        response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

        assert len(response.tool_calls) == 1
        assert provider.calls == 1
        assert delays == []

    @pytest.mark.asyncio
    async def test_reasoning_content_guard(self, monkeypatch) -> None:
        """Blank content but non-empty reasoning_content is returned immediately."""
        provider = ScriptedProvider(
            [LLMResponse(content="", finish_reason="stop", reasoning_content="thinking...")]
        )
        delays: list[float] = []
        await self._monkeypatch_sleep(monkeypatch, delays)

        response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

        assert response.content == ""
        assert response.reasoning_content == "thinking..."
        assert provider.calls == 1
        assert delays == []


class TestPoolFailoverBlank:
    @pytest.mark.asyncio
    async def test_pool_failover_over_blank_response(self) -> None:
        """Pool fails over from a blank (empty content) provider to the next."""
        first = ScriptedProvider([LLMResponse(content="", finish_reason="stop")])
        second = ScriptedProvider([LLMResponse(content="ok")])
        pool = ProviderPoolProvider(
            [
                ProviderPoolEntry(name="first", provider=first),
                ProviderPoolEntry(name="second", provider=second),
            ],
            strategy="failover",
            default_model="shared-model",
        )

        response = await pool.chat(messages=[{"role": "user", "content": "hello"}])

        assert response.content == "ok"
        assert first.calls == 1
        assert second.calls == 1
