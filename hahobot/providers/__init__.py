"""LLM provider abstraction module."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from hahobot.providers.base import LLMProvider, LLMResponse

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "AnthropicProvider",
    "OpenAICompatProvider",
    "OpenAICodexProvider",
    "GitHubCopilotProvider",
    "AzureOpenAIProvider",
    "ProviderPoolProvider",
]

_LAZY_IMPORTS = {
    "AnthropicProvider": ".anthropic_provider",
    "OpenAICompatProvider": ".openai_compat_provider",
    "OpenAICodexProvider": ".openai_codex_provider",
    "GitHubCopilotProvider": ".github_copilot_provider",
    "AzureOpenAIProvider": ".azure_openai_provider",
    "ProviderPoolProvider": ".pool_provider",
}

if TYPE_CHECKING:
    from hahobot.providers.anthropic_provider import AnthropicProvider
    from hahobot.providers.azure_openai_provider import AzureOpenAIProvider
    from hahobot.providers.github_copilot_provider import GitHubCopilotProvider
    from hahobot.providers.openai_codex_provider import OpenAICodexProvider
    from hahobot.providers.openai_compat_provider import OpenAICompatProvider
    from hahobot.providers.pool_provider import ProviderPoolProvider


def __getattr__(name: str):
    """Lazily expose provider implementations without importing all backends up front."""
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is not None:
        module = import_module(module_name, __name__)
        return getattr(module, name)

    # Preserve package-style submodule access such as `hahobot.providers.base`
    # so monkeypatch/import helpers can still resolve nested module paths.
    try:
        return import_module(f".{name}", __name__)
    except ModuleNotFoundError as exc:
        if exc.name not in {f"{__name__}.{name}", name}:
            raise
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
