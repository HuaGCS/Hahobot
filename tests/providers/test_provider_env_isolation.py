"""Provider credentials must not leak through process-global ``os.environ``."""

from __future__ import annotations

import os
from unittest.mock import patch

from hahobot.providers.openai_compat_provider import OpenAICompatProvider
from hahobot.providers.registry import find_by_name


def test_provider_init_does_not_mutate_shared_env_keys(monkeypatch) -> None:
    """Multi-provider setups must not overwrite or pin one another's credentials."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    openai_spec = find_by_name("openai")
    openrouter_spec = find_by_name("openrouter")
    assert openai_spec is not None and openrouter_spec is not None

    with patch("hahobot.providers.openai_compat_provider.AsyncOpenAI") as client_cls:
        openai = OpenAICompatProvider(
            api_key="sk-openai-secret",
            default_model="gpt-4o",
            spec=openai_spec,
        )
        OpenAICompatProvider(
            api_key="sk-or-secret",
            default_model="openrouter/auto",
            spec=openrouter_spec,
        )

    assert "OPENAI_API_KEY" not in os.environ
    assert "OPENROUTER_API_KEY" not in os.environ
    assert openai.api_key == "sk-openai-secret"
    assert [call.kwargs["api_key"] for call in client_cls.call_args_list] == [
        "sk-openai-secret",
        "sk-or-secret",
    ]


def test_gateway_provider_preserves_preexisting_env_key(monkeypatch) -> None:
    """Gateway specs used to overwrite their env key instead of using setdefault."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "preexisting-user-key")
    spec = find_by_name("openrouter")
    assert spec is not None

    with patch("hahobot.providers.openai_compat_provider.AsyncOpenAI") as client_cls:
        provider = OpenAICompatProvider(
            api_key="sk-from-config",
            default_model="openrouter/auto",
            spec=spec,
        )

    assert os.environ["OPENROUTER_API_KEY"] == "preexisting-user-key"
    assert provider.api_key == "sk-from-config"
    assert client_cls.call_args.kwargs["api_key"] == "sk-from-config"


def test_provider_init_does_not_publish_compatibility_aliases(monkeypatch) -> None:
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    spec = find_by_name("zhipu")
    assert spec is not None and spec.env_extras

    with patch("hahobot.providers.openai_compat_provider.AsyncOpenAI"):
        OpenAICompatProvider(api_key="zhipu-secret", default_model="glm-4", spec=spec)

    assert "ZAI_API_KEY" not in os.environ
    assert "ZHIPUAI_API_KEY" not in os.environ
