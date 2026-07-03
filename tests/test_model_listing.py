"""Tests for provider model-list resolution and fetching (admin model picker)."""

from __future__ import annotations

import pytest

from hahobot.config.schema import Config
from hahobot.providers.model_listing import (
    ModelListingError,
    _models_url_and_headers,
    _parse_model_ids,
    _resolve_provider,
    list_provider_models,
)


def _config_with_custom() -> Config:
    config = Config()
    config.agents.defaults.provider = "custom"
    config.agents.defaults.model = "deepseek-v4-flash-free"
    config.providers.custom.api_base = "http://10.0.1.130:1234/v1"
    config.providers.custom.api_key = "sk-local"
    return config


def test_parse_model_ids_dedupes_sorts_and_handles_shapes() -> None:
    assert _parse_model_ids({"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}]}) == ["a", "b"]
    assert _parse_model_ids({"models": [{"name": "Z"}, {"name": "y"}]}) == ["y", "Z"]
    assert _parse_model_ids(["m2", "m1", "m1"]) == ["m1", "m2"]
    assert _parse_model_ids({"data": [{}, {"id": ""}, {"id": "  ok "}]}) == ["ok"]
    assert _parse_model_ids("garbage") == []


def test_resolve_provider_uses_forced_provider_base_and_key() -> None:
    name, base, key = _resolve_provider(_config_with_custom(), None)
    assert name == "custom"
    assert base == "http://10.0.1.130:1234/v1"
    assert key == "sk-local"


def test_resolve_provider_openrouter_falls_back_to_registry_default_base() -> None:
    config = Config()
    config.providers.openrouter.api_key = "or-key"
    name, base, key = _resolve_provider(config, "openrouter")
    assert name == "openrouter"
    assert base == "https://openrouter.ai/api/v1"
    assert key == "or-key"


def test_resolve_provider_unknown_raises() -> None:
    with pytest.raises(ModelListingError):
        _resolve_provider(Config(), "does-not-exist")


def test_models_url_and_headers_openai_vs_anthropic() -> None:
    url, headers = _models_url_and_headers("custom", "http://10.0.1.130:1234/v1/", "k")
    assert url == "http://10.0.1.130:1234/v1/models"
    assert headers["Authorization"] == "Bearer k"
    aurl, aheaders = _models_url_and_headers("anthropic", "https://api.anthropic.com/v1", "k")
    assert aurl == "https://api.anthropic.com/v1/models"
    assert aheaders["x-api-key"] == "k"
    assert "anthropic-version" in aheaders


async def test_list_provider_models_end_to_end_with_stub() -> None:
    seen: dict[str, object] = {}

    async def _stub(url: str, headers: dict[str, str], timeout: float):
        seen["url"] = url
        seen["headers"] = headers
        return {"data": [{"id": "qwen2.5-7b"}, {"id": "deepseek-v4-flash-free"}]}

    models = await list_provider_models(_config_with_custom(), None, get_json=_stub)
    assert models == ["deepseek-v4-flash-free", "qwen2.5-7b"]
    assert seen["url"] == "http://10.0.1.130:1234/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer sk-local"


async def test_list_provider_models_wraps_transport_errors() -> None:
    async def _boom(url: str, headers: dict[str, str], timeout: float):
        raise ConnectionError("refused")

    with pytest.raises(ModelListingError):
        await list_provider_models(_config_with_custom(), None, get_json=_boom)


async def test_list_provider_models_requires_api_base() -> None:
    config = Config()
    config.agents.defaults.provider = "custom"  # custom has no default base and none set
    with pytest.raises(ModelListingError):
        await list_provider_models(config, None, get_json=None)
