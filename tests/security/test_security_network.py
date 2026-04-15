"""Tests for hahobot.security.network — SSRF protection and internal URL detection."""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, patch

import pytest

from hahobot.security.network import (
    configure_ssrf_whitelist,
    contains_internal_url,
    validate_url_target,
)


def _fake_resolve(host: str, results: list[str]):
    """Return a getaddrinfo mock that maps the given host to fake IP results."""
    def _resolver(hostname, port, family=0, type_=0):
        if hostname == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in results]
        raise socket.gaierror(f"cannot resolve {hostname}")
    return _resolver


def _patch_resolve(host: str, results: list[str]):
    """Patch _resolve_hostname (the async wrapper) with an AsyncMock."""
    resolved = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in results]
    return patch("hahobot.security.network._resolve_hostname", AsyncMock(return_value=resolved))


def _patch_resolve_raises(exc: Exception):
    return patch("hahobot.security.network._resolve_hostname", AsyncMock(side_effect=exc))


# ---------------------------------------------------------------------------
# validate_url_target — scheme / domain basics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rejects_non_http_scheme():
    ok, err = await validate_url_target("ftp://example.com/file")
    assert not ok
    assert "http" in err.lower()


@pytest.mark.asyncio
async def test_rejects_missing_domain():
    ok, err = await validate_url_target("http://")
    assert not ok


# ---------------------------------------------------------------------------
# validate_url_target — blocked private/internal IPs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("ip,label", [
    ("127.0.0.1", "loopback"),
    ("127.0.0.2", "loopback_alt"),
    ("10.0.0.1", "rfc1918_10"),
    ("172.16.5.1", "rfc1918_172"),
    ("192.168.1.1", "rfc1918_192"),
    ("169.254.169.254", "metadata"),
    ("0.0.0.0", "zero"),
])
async def test_blocks_private_ipv4(ip: str, label: str):
    with _patch_resolve("evil.com", [ip]):
        ok, err = await validate_url_target("http://evil.com/path")
        assert not ok, f"Should block {label} ({ip})"
        assert "private" in err.lower() or "blocked" in err.lower()


@pytest.mark.asyncio
async def test_blocks_ipv6_loopback():
    resolved = [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0))]
    with patch("hahobot.security.network._resolve_hostname", AsyncMock(return_value=resolved)):
        ok, err = await validate_url_target("http://evil.com/")
    assert not ok


@pytest.mark.asyncio
async def test_blocks_ipv4_mapped_ipv6_loopback():
    resolved = [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::ffff:127.0.0.1", 0, 0, 0))]
    with patch("hahobot.security.network._resolve_hostname", AsyncMock(return_value=resolved)):
        ok, err = await validate_url_target("http://evil.com/")
    assert not ok
    assert "127.0.0.1" in err


# ---------------------------------------------------------------------------
# validate_url_target — allows public IPs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_allows_public_ip():
    with _patch_resolve("example.com", ["93.184.216.34"]):
        ok, err = await validate_url_target("http://example.com/page")
        assert ok, f"Should allow public IP, got: {err}"


@pytest.mark.asyncio
async def test_allows_normal_https():
    with _patch_resolve("github.com", ["140.82.121.3"]):
        ok, err = await validate_url_target("https://github.com/HKUDS/hahobot")
        assert ok


# ---------------------------------------------------------------------------
# contains_internal_url — shell command scanning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detects_curl_metadata():
    with _patch_resolve("169.254.169.254", ["169.254.169.254"]):
        assert await contains_internal_url('curl -s http://169.254.169.254/computeMetadata/v1/')


@pytest.mark.asyncio
async def test_detects_wget_localhost():
    with _patch_resolve("localhost", ["127.0.0.1"]):
        assert await contains_internal_url("wget http://localhost:8080/secret")


@pytest.mark.asyncio
async def test_allows_normal_curl():
    with _patch_resolve("example.com", ["93.184.216.34"]):
        assert not await contains_internal_url("curl https://example.com/api/data")


@pytest.mark.asyncio
async def test_no_urls_returns_false():
    assert not await contains_internal_url("echo hello && ls -la")


# ---------------------------------------------------------------------------
# SSRF whitelist — allow specific CIDR ranges (#2669)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blocks_cgnat_by_default():
    """100.64.0.0/10 (CGNAT / Tailscale) is blocked by default."""
    with _patch_resolve("ts.local", ["100.100.1.1"]):
        ok, _ = await validate_url_target("http://ts.local/api")
        assert not ok


@pytest.mark.asyncio
async def test_whitelist_allows_cgnat():
    """Whitelisting 100.64.0.0/10 lets Tailscale addresses through."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    try:
        with _patch_resolve("ts.local", ["100.100.1.1"]):
            ok, err = await validate_url_target("http://ts.local/api")
            assert ok, f"Whitelisted CGNAT should be allowed, got: {err}"
    finally:
        configure_ssrf_whitelist([])


@pytest.mark.asyncio
async def test_whitelist_does_not_affect_other_blocked():
    """Whitelisting CGNAT must not unblock other private ranges."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    try:
        with _patch_resolve("evil.com", ["10.0.0.1"]):
            ok, _ = await validate_url_target("http://evil.com/secret")
            assert not ok
    finally:
        configure_ssrf_whitelist([])


@pytest.mark.asyncio
async def test_whitelist_invalid_cidr_ignored():
    """Invalid CIDR entries are silently skipped."""
    configure_ssrf_whitelist(["not-a-cidr", "100.64.0.0/10"])
    try:
        with _patch_resolve("ts.local", ["100.100.1.1"]):
            ok, _ = await validate_url_target("http://ts.local/api")
            assert ok
    finally:
        configure_ssrf_whitelist([])
