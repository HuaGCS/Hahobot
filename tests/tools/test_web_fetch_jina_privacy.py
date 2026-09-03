"""Credential-boundary regressions for the remote Jina Reader path."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hahobot.agent.tools import web as web_module
from hahobot.agent.tools.web import (
    WebFetchTool,
    _get_with_safe_redirects,
    _redact_url_for_log,
    _url_carries_credentials,
)


class _RecordingJinaClient:
    requested: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        self.requested.append(str(url))

        class _Response:
            status_code = 200

            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json() -> dict:
                return {"data": {"title": "T", "content": "body", "url": str(url)}}

        return _Response()


@pytest.fixture
def jina_client(monkeypatch):
    _RecordingJinaClient.requested = []
    monkeypatch.setattr(web_module.httpx, "AsyncClient", _RecordingJinaClient)
    return _RecordingJinaClient


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.com/report",
        "https://user@example.com/report",
        "https://example.com/download?token=abc123",
        "https://example.com/download?access_token=abc123",
        "https://example.com/doc?Signature=xyz&Expires=1700000000",
        "https://bucket.example.com/key?X-Amz-Signature=deadbeef",
        "https://storage.example.com/file?X-Goog-Signature=deadbeef",
        "https://example.com/callback?code=oauth-code",
        "https://example.com/download?API-KEY=secret",
        "https://example.com/download?file=report;token=secret",
    ],
)
def test_credential_urls_are_detected(url: str) -> None:
    assert _url_carries_credentials(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://example.com/watch?v=abc123",
        "https://example.com/search?q=token+design&page=2",
        "https://example.com/page#section-3",
    ],
)
def test_plain_urls_are_not_detected(url: str) -> None:
    assert _url_carries_credentials(url) is False


def test_log_label_excludes_credentials_and_path() -> None:
    url = "https://user:secret@example.com:8443/private/token?token=abc#secret"
    assert _redact_url_for_log(url) == "https://example.com:8443"


def test_log_label_preserves_ipv6_origin() -> None:
    url = "https://user:secret@[2001:db8::1]:8443/private?token=abc"
    assert _redact_url_for_log(url) == "https://[2001:db8::1]:8443"


async def test_jina_is_skipped_for_credential_url(jina_client) -> None:
    result = await WebFetchTool()._fetch_jina(
        "https://example.com/download?token=abc123",
        max_chars=1000,
    )

    assert result is None
    assert jina_client.requested == []


async def test_jina_skip_log_does_not_contain_credentials(jina_client, monkeypatch) -> None:
    logged: list[tuple[object, ...]] = []
    monkeypatch.setattr(web_module.logger, "debug", lambda *args: logged.append(args))

    result = await WebFetchTool()._fetch_jina(
        "https://user:secret@example.com/private/webhook-token?token=abc",
        max_chars=1000,
    )

    assert result is None
    rendered = " ".join(str(item) for call in logged for item in call)
    assert "secret" not in rendered
    assert "webhook-token" not in rendered
    assert "token=abc" not in rendered


async def test_jina_keeps_plain_urls_but_drops_fragment(jina_client) -> None:
    result = await WebFetchTool()._fetch_jina(
        "https://example.com/page?q=1#access_token=leaked",
        max_chars=1000,
    )

    assert json.loads(result or "{}")["extractor"] == "jina"
    assert jina_client.requested == ["https://r.jina.ai/https://example.com/page?q=1"]


async def test_execute_keeps_credential_redirect_chain_local(monkeypatch) -> None:
    tool = WebFetchTool()
    response = SimpleNamespace(headers={"content-type": "text/html"})
    get_safe = AsyncMock(return_value=(response, None, True))
    jina = AsyncMock(return_value="remote")
    readability = AsyncMock(return_value="local")

    class _ContextClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(web_module, "_validate_url_safe", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(web_module, "_get_with_safe_redirects", get_safe)
    monkeypatch.setattr(web_module.httpx, "AsyncClient", _ContextClient)
    monkeypatch.setattr(tool, "_fetch_jina", jina)
    monkeypatch.setattr(tool, "_fetch_readability", readability)

    result = await tool.execute(url="https://example.com/short")

    assert result == "local"
    jina.assert_not_awaited()
    readability.assert_awaited_once()


async def test_redirect_preflight_marks_credential_hop(monkeypatch) -> None:
    short_url = "https://example.com/short"
    signed_url = "https://cdn.example.com/file?token=secret"

    class _Response:
        def __init__(self, url: str) -> None:
            self.url = url
            self.is_redirect = url == short_url
            self.headers = {"location": signed_url} if self.is_redirect else {}
            self.aclose = AsyncMock()

    class _Client:
        requested: list[str] = []

        async def get(self, url, **kwargs):
            self.requested.append(str(url))
            return _Response(str(url))

    monkeypatch.setattr(web_module, "_validate_url_safe", AsyncMock(return_value=(True, "")))
    client = _Client()

    response, error, carries_credentials = await _get_with_safe_redirects(client, short_url)

    assert error is None
    assert response is not None
    assert client.requested == [short_url, signed_url]
    assert carries_credentials is True


async def test_execute_does_not_delegate_when_local_preflight_fails(monkeypatch) -> None:
    tool = WebFetchTool()
    jina = AsyncMock(return_value="remote")
    readability = AsyncMock(return_value="local")

    class _FailingClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            raise RuntimeError("offline")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(web_module, "_validate_url_safe", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(web_module.httpx, "AsyncClient", _FailingClient)
    monkeypatch.setattr(tool, "_fetch_jina", jina)
    monkeypatch.setattr(tool, "_fetch_readability", readability)

    result = await tool.execute(url="https://example.com/plain")

    assert result == "local"
    jina.assert_not_awaited()
    readability.assert_awaited_once()
