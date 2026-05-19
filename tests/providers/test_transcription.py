"""Tests for transcription retry behavior on transient errors."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hahobot.providers.transcription import GroqTranscriptionProvider, OpenAITranscriptionProvider


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "voice.ogg"
    path.write_bytes(b"OggS\x00fake-audio-bytes")
    return path


def _response(status: int, payload: dict[str, object] | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://example.test/audio/transcriptions")
    return httpx.Response(status_code=status, json=payload or {}, request=request)


def _raw_response(status: int, content: bytes) -> httpx.Response:
    request = httpx.Request("POST", "https://example.test/audio/transcriptions")
    return httpx.Response(status_code=status, content=content, request=request)


@pytest.mark.asyncio
async def test_openai_retries_on_5xx_then_succeeds(audio_file: Path) -> None:
    provider = OpenAITranscriptionProvider(api_key="sk-test")
    post = AsyncMock(side_effect=[_response(503), _response(200, {"text": "hello"})])

    with patch("httpx.AsyncClient.post", post), patch("asyncio.sleep", AsyncMock()):
        result = await provider.transcribe(audio_file)

    assert result == "hello"
    assert post.await_count == 2


@pytest.mark.asyncio
async def test_groq_retries_on_connection_error(audio_file: Path) -> None:
    provider = GroqTranscriptionProvider(api_key="gsk-test")
    post = AsyncMock(
        side_effect=[httpx.ConnectError("boom"), _response(200, {"text": "groq ok"})],
    )

    with patch("httpx.AsyncClient.post", post), patch("asyncio.sleep", AsyncMock()):
        result = await provider.transcribe(audio_file)

    assert result == "groq ok"
    assert post.await_count == 2


@pytest.mark.asyncio
async def test_transcription_does_not_retry_auth_error(audio_file: Path) -> None:
    provider = OpenAITranscriptionProvider(api_key="sk-test")
    post = AsyncMock(return_value=_response(401, {"error": {"message": "bad key"}}))

    with patch("httpx.AsyncClient.post", post), patch("asyncio.sleep", AsyncMock()):
        result = await provider.transcribe(audio_file)

    assert result == ""
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_transcription_gives_up_after_max_attempts(audio_file: Path) -> None:
    provider = OpenAITranscriptionProvider(api_key="sk-test")
    post = AsyncMock(return_value=_response(503))
    sleep = AsyncMock()

    with patch("httpx.AsyncClient.post", post), patch("asyncio.sleep", sleep):
        result = await provider.transcribe(audio_file)

    assert result == ""
    assert post.await_count == 4
    assert [call.args[0] for call in sleep.await_args_list] == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_transcription_missing_api_key_short_circuits(audio_file: Path) -> None:
    with patch.dict("os.environ", {}, clear=True):
        provider = OpenAITranscriptionProvider(api_key=None)
        post = AsyncMock()
        with patch("httpx.AsyncClient.post", post):
            result = await provider.transcribe(audio_file)

    assert result == ""
    assert post.await_count == 0


@pytest.mark.asyncio
async def test_transcription_unreadable_file_short_circuits(audio_file: Path) -> None:
    provider = OpenAITranscriptionProvider(api_key="sk-test")
    post = AsyncMock()

    with (
        patch("pathlib.Path.read_bytes", side_effect=PermissionError("denied")),
        patch(
            "httpx.AsyncClient.post",
            post,
        ),
    ):
        result = await provider.transcribe(audio_file)

    assert result == ""
    assert post.await_count == 0


@pytest.mark.parametrize(
    ("provider_cls", "language"),
    [(OpenAITranscriptionProvider, "en"), (GroqTranscriptionProvider, "zh")],
    ids=["openai", "groq"],
)
@pytest.mark.asyncio
async def test_transcription_forwards_language_on_every_attempt(
    audio_file: Path,
    provider_cls: type[OpenAITranscriptionProvider] | type[GroqTranscriptionProvider],
    language: str,
) -> None:
    provider = provider_cls(api_key="key", language=language)
    post = AsyncMock(side_effect=[_response(503), _response(200, {"text": "ok"})])

    with patch("httpx.AsyncClient.post", post), patch("asyncio.sleep", AsyncMock()):
        result = await provider.transcribe(audio_file)

    assert result == "ok"
    assert post.await_count == 2
    for call in post.await_args_list:
        assert call.kwargs["files"]["language"] == (None, language)


@pytest.mark.asyncio
async def test_transcription_returns_empty_on_malformed_json(audio_file: Path) -> None:
    provider = OpenAITranscriptionProvider(api_key="sk-test")
    post = AsyncMock(return_value=_raw_response(200, b"<html>not json</html>"))

    with patch("httpx.AsyncClient.post", post), patch("asyncio.sleep", AsyncMock()):
        result = await provider.transcribe(audio_file)

    assert result == ""
    assert post.await_count == 1


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
@pytest.mark.asyncio
async def test_transcription_retries_retryable_statuses(audio_file: Path, status: int) -> None:
    provider = OpenAITranscriptionProvider(api_key="sk-test")
    post = AsyncMock(side_effect=[_response(status), _response(200, {"text": "ok"})])

    with patch("httpx.AsyncClient.post", post), patch("asyncio.sleep", AsyncMock()):
        result = await provider.transcribe(audio_file)

    assert result == "ok"
    assert post.await_count == 2
