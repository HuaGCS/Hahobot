from typing import Any

import pytest

from hahobot.agent.tools import web as web_module
from hahobot.agent.tools.web import WebSearchTool
from hahobot.config.schema import Config


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_web_search_tool_brave_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    payload = {
        "web": {
            "results": [
                {
                    "title": "Hahobot",
                    "url": "https://example.com/hahobot",
                    "description": "A lightweight personal AI assistant.",
                }
            ]
        }
    }

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.proxy = kwargs.get("proxy")

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: float | None = None,
        ) -> _FakeResponse:
            calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
            return _FakeResponse(payload)

    monkeypatch.setattr(web_module.httpx, "AsyncClient", _FakeAsyncClient)

    tool = WebSearchTool(provider="brave", api_key="test-key")
    result = await tool.execute(query="hahobot", count=3)

    assert "Hahobot" in result
    assert "https://example.com/hahobot" in result
    assert "A lightweight personal AI assistant." in result
    assert calls == [
        {
            "url": "https://api.search.brave.com/res/v1/web/search",
            "params": {"q": "hahobot", "count": 3},
            "headers": {"Accept": "application/json", "X-Subscription-Token": "test-key"},
            "timeout": 10.0,
        }
    ]


@pytest.mark.asyncio
async def test_web_search_tool_searxng_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    payload = {
        "results": [
            {
                "title": "Hahobot Docs",
                "url": "https://example.com/docs",
                "content": "Self-hosted search works.",
            }
        ]
    }

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.proxy = kwargs.get("proxy")

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: float | None = None,
        ) -> _FakeResponse:
            calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
            return _FakeResponse(payload)

    monkeypatch.setattr(web_module.httpx, "AsyncClient", _FakeAsyncClient)

    tool = WebSearchTool(provider="searxng", base_url="http://localhost:8080")
    result = await tool.execute(query="hahobot", count=4)

    assert "Hahobot Docs" in result
    assert "https://example.com/docs" in result
    assert "Self-hosted search works." in result
    assert calls == [
        {
            "url": "http://localhost:8080/search",
            "params": {"q": "hahobot", "format": "json"},
            "headers": {"Accept": "application/json"},
            "timeout": 10.0,
        }
    ]


def test_web_search_tool_searxng_keeps_explicit_search_path() -> None:
    tool = WebSearchTool(provider="searxng", base_url="https://search.example.com/search/")

    assert tool._build_searxng_search_url() == "https://search.example.com/search"


def test_web_search_config_accepts_searxng_fields() -> None:
    config = Config.model_validate(
        {
            "tools": {
                "web": {
                    "search": {
                        "provider": "searxng",
                        "baseUrl": "http://localhost:8080",
                        "maxResults": 7,
                    }
                }
            }
        }
    )

    assert config.tools.web.search.provider == "searxng"
    assert config.tools.web.search.base_url == "http://localhost:8080"
    assert config.tools.web.search.max_results == 7


def test_web_search_config_accepts_duckduckgo_provider() -> None:
    config = Config.model_validate(
        {
            "tools": {
                "web": {
                    "search": {
                        "provider": "duckduckgo",
                    }
                }
            }
        }
    )

    assert config.tools.web.search.provider == "duckduckgo"


@pytest.mark.asyncio
async def test_web_search_tool_duckduckgo_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    payload = """
    <html><body>
      <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fduck">Duck Result</a>
      <div class="result__snippet">Duck snippet content.</div>
    </body></html>
    """

    class _FakeDuckResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.proxy = kwargs.get("proxy")
            self.follow_redirects = kwargs.get("follow_redirects")

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: float | None = None,
        ) -> _FakeDuckResponse:
            calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
            return _FakeDuckResponse(payload)

    monkeypatch.setattr(web_module.httpx, "AsyncClient", _FakeAsyncClient)

    tool = WebSearchTool(provider="duckduckgo")
    result = await tool.execute(query="hahobot", count=2)

    assert tool.exclusive is True
    assert "Duck Result" in result
    assert "https://example.com/duck" in result
    assert "Duck snippet content." in result
    assert calls == [
        {
            "url": "https://html.duckduckgo.com/html/",
            "params": {"q": "hahobot"},
            "headers": {"User-Agent": web_module.USER_AGENT},
            "timeout": 10.0,
        }
    ]


@pytest.mark.asyncio
async def test_web_search_tool_uses_env_provider_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    payload = {
        "results": [
            {
                "title": "Hahobot Env",
                "url": "https://example.com/env",
                "content": "Resolved from environment variables.",
            }
        ]
    }

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.proxy = kwargs.get("proxy")

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: float | None = None,
        ) -> _FakeResponse:
            calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
            return _FakeResponse(payload)

    monkeypatch.setattr(web_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "http://localhost:9090")

    tool = WebSearchTool()
    result = await tool.execute(query="hahobot", count=2)

    assert "Hahobot Env" in result
    assert calls == [
        {
            "url": "http://localhost:9090/search",
            "params": {"q": "hahobot", "format": "json"},
            "headers": {"Accept": "application/json"},
            "timeout": 10.0,
        }
    ]
