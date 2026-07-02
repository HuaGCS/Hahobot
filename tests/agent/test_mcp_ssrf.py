"""Redirect-SSRF guard tests for MCP HTTP/SSE transports.

Adapted from nanobot ed0aeb1e, but scoped to hahobot's threat model: an MCP
server URL is *operator-configured* in config.json, so the configured host is
trusted even when it is loopback/LAN (local MCP servers are the common case).
The only vector left is a configured-public server that *redirects* to an
internal address, so only cross-host redirects to private targets are blocked.
"""

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from hahobot.agent.tools.mcp import _make_mcp_redirect_validator, _redact_url


def _http_cfg(url: str) -> SimpleNamespace:
    return SimpleNamespace(
        type=None,
        command=None,
        args=None,
        env=None,
        url=url,
        headers=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:3211/mcp",
        "http://localhost:8931/mcp",
        "http://192.168.1.50:9000/sse",
        "http://[::1]/mcp",
    ],
)
async def test_configured_local_url_is_allowed(url: str) -> None:
    """A request to the operator-configured host (incl. localhost/LAN) is never blocked."""
    validate = _make_mcp_redirect_validator(url)
    request = httpx.Request("POST", url)
    await validate(request)  # must not raise


@pytest.mark.asyncio
async def test_redirect_to_internal_host_is_blocked() -> None:
    """A redirect from the configured host to a *different* internal host is rejected."""
    validate = _make_mcp_redirect_validator("https://mcp.example.com/mcp")
    redirect = httpx.Request("GET", "http://169.254.169.254/latest/meta-data/")
    with pytest.raises(httpx.RequestError, match="Blocked MCP redirect to unsafe URL"):
        await validate(redirect)


@pytest.mark.asyncio
async def test_redirect_to_loopback_from_public_host_is_blocked() -> None:
    """A configured-public server redirecting to loopback is rejected."""
    validate = _make_mcp_redirect_validator("https://mcp.example.com/mcp")
    redirect = httpx.Request("GET", "http://127.0.0.1:9/admin")
    with pytest.raises(httpx.RequestError, match="Blocked MCP redirect to unsafe URL"):
        await validate(redirect)


@pytest.mark.asyncio
async def test_redirect_to_public_host_is_allowed() -> None:
    """A redirect to a different public host passes (IP check returns ok)."""
    validate = _make_mcp_redirect_validator("https://mcp.example.com/mcp")
    redirect = httpx.Request("GET", "https://cdn.example.net/mcp")
    with patch(
        "hahobot.agent.tools.mcp.validate_resolved_url",
        return_value=(True, ""),
    ):
        await validate(redirect)  # must not raise


@pytest.mark.asyncio
async def test_same_host_redirect_is_allowed_even_if_local() -> None:
    """A redirect that stays on the configured local host is trusted (no IP recheck)."""
    validate = _make_mcp_redirect_validator("http://127.0.0.1:3211/mcp")
    redirect = httpx.Request("GET", "http://127.0.0.1:3211/mcp/stream")
    await validate(redirect)  # must not raise


# --- URL redaction before logging (nanobot 780093d0 / bfc2a74e / f9b02496) ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        # userinfo credentials are dropped
        ("https://user:token@host.example.com/sse", "https://host.example.com/..."),
        # query/fragment secrets are dropped
        ("https://host.example.com/mcp?token=SECRET#frag", "https://host.example.com/..."),
        # opaque path token becomes a placeholder
        ("https://host.example.com/AbC123SecretPath", "https://host.example.com/..."),
        # port is preserved
        ("http://127.0.0.1:3211/mcp", "http://127.0.0.1:3211/..."),
        # bare origin / root path stays intact
        ("https://host.example.com/", "https://host.example.com/"),
        ("https://host.example.com", "https://host.example.com"),
        # IPv6 host keeps its brackets
        ("http://[::1]:9000/mcp?k=v", "http://[::1]:9000/..."),
    ],
)
def test_redact_url_strips_secrets(raw: str, expected: str) -> None:
    assert _redact_url(raw) == expected


@pytest.mark.asyncio
async def test_blocked_redirect_message_does_not_leak_credentials() -> None:
    """The blocked-redirect error must not echo userinfo/query secrets into logs."""
    validate = _make_mcp_redirect_validator("https://mcp.example.com/mcp")
    redirect = httpx.Request("GET", "http://user:s3cr3t@169.254.169.254/meta?token=abc")
    with pytest.raises(httpx.RequestError) as exc:
        await validate(redirect)
    message = str(exc.value)
    assert "s3cr3t" not in message
    assert "token=abc" not in message
    assert "169.254.169.254" in message  # host itself is still useful for triage
