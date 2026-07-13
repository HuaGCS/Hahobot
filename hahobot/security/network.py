"""Network security utilities — SSRF protection and internal URL detection."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from contextlib import contextmanager
from urllib.parse import urlparse
from urllib.request import getproxies

import httpx

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local v6
]

_URL_RE = re.compile(r"https?://[^\s\"'`;|<>]+", re.IGNORECASE)

_allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []


def configure_ssrf_whitelist(cidrs: list[str]) -> None:
    """Allow specific CIDR ranges to bypass SSRF blocking (e.g. Tailscale's 100.64.0.0/10)."""
    global _allowed_networks
    nets = []
    for cidr in cidrs:
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            pass
    _allowed_networks = nets


def _normalize_addr(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    # Normalize IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1) to their
    # IPv4 equivalent so they are correctly matched against _BLOCKED_NETWORKS.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def _is_private(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    normalized = _normalize_addr(addr)
    if _allowed_networks and any(normalized in net for net in _allowed_networks):
        return False
    return any(normalized in net for net in _BLOCKED_NETWORKS)


async def _resolve_hostname(hostname: str) -> list:
    """Resolve hostname to address infos without blocking the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM),
    )


async def resolve_url_target(
    url: str,
    *,
    allow_loopback: bool = False,
) -> tuple[bool, str, tuple[str, ...]]:
    """Validate a URL is safe to fetch: scheme, hostname, and resolved IPs.

    Returns (ok, error_message, resolved_ips).  When ok is True, resolved_ips
    contains the addresses validated for this URL so direct HTTP transports can
    pin subsequent DNS lookups and avoid DNS rebinding between validation and
    connect.
    """
    try:
        p = urlparse(url)
    except Exception as e:
        return False, str(e), ()

    if p.scheme not in ("http", "https"):
        return False, f"Only http/https allowed, got '{p.scheme or 'none'}'", ()
    if not p.netloc:
        return False, "Missing domain", ()

    hostname = p.hostname
    if not hostname:
        return False, "Missing hostname", ()

    try:
        infos = await _resolve_hostname(hostname)
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}", ()

    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        addrs.append(addr)

    if allow_loopback and _is_allowed_loopback_target(hostname, addrs):
        resolved = tuple(dict.fromkeys(str(_normalize_addr(addr)) for addr in addrs))
        return True, "", resolved

    for addr in addrs:
        if _is_private(addr):
            return False, f"Blocked: {hostname} resolves to private/internal address {addr}", ()

    resolved = tuple(dict.fromkeys(str(_normalize_addr(addr)) for addr in addrs))
    return True, "", resolved


async def validate_url_target(url: str) -> tuple[bool, str]:
    """Validate a URL is safe to fetch: scheme, hostname, and resolved IPs.

    Returns (ok, error_message).  When ok is True, error_message is empty.
    """
    ok, error, _ = await resolve_url_target(url)
    return ok, error


@contextmanager
def pin_resolved_url_dns(url: str, resolved_ips: tuple[str, ...]):
    """Temporarily pin DNS lookups for ``url`` to already validated IPs.

    This overrides process-global resolver state, so callers must serialize
    access across awaits. Prefer ``PinnedDNSAsyncTransport`` for HTTPX clients.
    """
    try:
        hostname = urlparse(url).hostname
    except Exception:
        hostname = None
    if not hostname or not resolved_ips:
        yield
        return

    pinned_host = hostname.rstrip(".").lower()
    original_getaddrinfo = socket.getaddrinfo

    def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        if str(host).rstrip(".").lower() != pinned_host:
            return original_getaddrinfo(host, port, family, type, proto, flags)
        infos = []
        for ip in resolved_ips:
            addr = ipaddress.ip_address(ip)
            addr_family = socket.AF_INET6 if addr.version == 6 else socket.AF_INET
            if family not in (0, socket.AF_UNSPEC, addr_family):
                continue
            sockaddr = (ip, port or 0, 0, 0) if addr_family == socket.AF_INET6 else (ip, port or 0)
            infos.append((addr_family, type or socket.SOCK_STREAM, proto, "", sockaddr))
        return infos

    socket.getaddrinfo = _getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


class UnsafeURLRequestError(httpx.RequestError):
    """Raised when an outgoing request is rejected by URL safety validation."""


def httpx_env_proxy_mounts() -> dict[str, httpx.AsyncBaseTransport]:
    """Build HTTPX proxy mounts from standard environment proxy settings."""
    proxies = getproxies()
    mounts: dict[str, httpx.AsyncBaseTransport] = {}
    for scheme in ("http", "https", "all"):
        proxy_url = proxies.get(scheme)
        if not proxy_url:
            continue
        if "://" not in proxy_url:
            proxy_url = f"http://{proxy_url}"
        mounts[f"{scheme}://"] = httpx.AsyncHTTPTransport(proxy=httpx.Proxy(proxy_url))
    return mounts


class PinnedDNSAsyncTransport(httpx.AsyncBaseTransport):
    """HTTPX transport that pins each request to the IPs validated for its URL."""

    _resolver_lock = asyncio.Lock()

    def __init__(
        self,
        *,
        allow_loopback: bool = False,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._allow_loopback = allow_loopback
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        ok, error, resolved_ips = await resolve_url_target(
            url,
            allow_loopback=self._allow_loopback,
        )
        if not ok:
            raise UnsafeURLRequestError(error, request=request)
        async with self._resolver_lock:
            with pin_resolved_url_dns(url, resolved_ips):
                return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


async def validate_resolved_url(url: str) -> tuple[bool, str]:
    """Validate an already-fetched URL (e.g. after redirect). Only checks the IP, skips DNS."""
    try:
        p = urlparse(url)
    except Exception:
        return True, ""

    hostname = p.hostname
    if not hostname:
        return True, ""

    try:
        addr = ipaddress.ip_address(hostname)
        normalized = _normalize_addr(addr)
        if _is_private(normalized):
            return False, f"Redirect target is a private address: {normalized}"
    except ValueError:
        # hostname is a domain name, resolve it
        try:
            infos = await _resolve_hostname(hostname)
        except socket.gaierror:
            return True, ""
        for info in infos:
            try:
                addr = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if _is_private(addr):
                return False, f"Redirect target {hostname} resolves to private address {addr}"

    return True, ""


async def contains_internal_url(command: str) -> bool:
    """Return True if the command string contains a URL targeting an internal/private address."""
    for m in _URL_RE.finditer(command):
        url = m.group(0)
        ok, _ = await validate_url_target(url)
        if not ok:
            return True
    return False


def _is_allowed_loopback_target(
    hostname: str,
    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> bool:
    if not addrs or not all(_normalize_addr(addr).is_loopback for addr in addrs):
        return False
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
