"""Fetch the available model list from a configured provider's OpenAI-compatible
``/models`` endpoint, for the admin config UI's model picker.

Security note: the endpoint host comes from the *operator-configured* provider
``api_base`` (or the registry default for that provider), never from
model/user-chosen input — the request only selects *which* configured provider to
query. This mirrors the MCP rule of trusting the operator-configured server host
(see ``hahobot/security/network.py``), so a private/LAN provider base (e.g. a
local LM Studio at ``http://10.0.1.130:1234/v1``) is intentionally allowed here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from hahobot.providers.registry import find_by_name

if TYPE_CHECKING:
    from hahobot.config.schema import Config

# Injection seam so tests can stub the network without a live provider.
JsonGetter = Callable[[str, dict[str, str], float], Awaitable[Any]]


class ModelListingError(Exception):
    """Raised when the model list cannot be resolved or fetched."""


def _resolve_provider(config: Config, provider_name: str | None) -> tuple[str, str, str | None]:
    """Return (registry_name, api_base, api_key) for the provider to query."""
    name = (
        provider_name if provider_name and provider_name != "auto" else config.get_provider_name()
    )
    if not name:
        raise ModelListingError("No provider is configured to resolve a model list from.")
    spec = find_by_name(name)
    if spec is None:
        raise ModelListingError(f"Unknown provider: {name}")
    pconf = getattr(config.providers, spec.name, None)
    api_base = (pconf.api_base if pconf and pconf.api_base else spec.default_api_base) or ""
    if not api_base:
        raise ModelListingError(f"Provider '{spec.name}' has no api_base configured.")
    api_key = pconf.api_key if pconf and pconf.api_key else None
    return spec.name, api_base, api_key


def _models_url_and_headers(
    name: str, api_base: str, api_key: str | None
) -> tuple[str, dict[str, str]]:
    base = api_base.rstrip("/")
    spec = find_by_name(name)
    backend = spec.backend if spec else "openai_compat"
    if backend == "anthropic":
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        return f"{base}/models", headers
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return f"{base}/models", headers


def _parse_model_ids(payload: Any) -> list[str]:
    """Extract sorted, de-duplicated model ids from an OpenAI/Anthropic response."""
    if isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    ids: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            mid = item.get("id") or item.get("name")
        else:
            mid = item
        if isinstance(mid, str) and mid.strip():
            ids.add(mid.strip())
    return sorted(ids, key=str.lower)


async def _default_get_json(url: str, headers: dict[str, str], timeout: float) -> Any:
    import aiohttp

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                body = (await resp.text())[:200]
                raise ModelListingError(f"Provider returned HTTP {resp.status}: {body}")
            return await resp.json(content_type=None)


async def list_provider_models(
    config: Config,
    provider_name: str | None = None,
    *,
    timeout: float = 10.0,
    get_json: JsonGetter | None = None,
) -> list[str]:
    """Return the model ids advertised by the resolved provider's ``/models``.

    ``provider_name`` selects which configured provider to query; ``None``/``"auto"``
    falls back to the provider the agent would actually use. Raises
    :class:`ModelListingError` on any resolution/fetch/parse failure.
    """
    name, api_base, api_key = _resolve_provider(config, provider_name)
    url, headers = _models_url_and_headers(name, api_base, api_key)
    fetch = get_json or _default_get_json
    try:
        payload = await fetch(url, headers, timeout)
    except ModelListingError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize transport errors for the UI
        raise ModelListingError(f"Failed to reach provider: {exc}") from exc
    return _parse_model_ids(payload)
