"""Config, provider and runtime-bootstrap helpers for CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from loguru import logger

from hahobot.cli.commands.interactive import console
from hahobot.config.paths import get_workspace_path as get_workspace_path
from hahobot.config.schema import Config
from hahobot.utils.helpers import sync_workspace_templates as sync_workspace_templates


def _merge_missing_defaults(existing: Any, defaults: Any) -> Any:
    """Recursively fill in missing values from defaults without overwriting user config."""
    if not isinstance(existing, dict) or not isinstance(defaults, dict):
        return existing

    merged = dict(existing)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = value
        else:
            merged[key] = _merge_missing_defaults(merged[key], value)
    return merged


def _resolve_channel_default_config(channel_cls: Any) -> dict[str, Any] | None:
    """Return a channel's default config if it exposes a valid onboarding payload."""

    default_config = getattr(channel_cls, "default_config", None)
    if not callable(default_config):
        return None
    try:
        payload = default_config()
    except Exception as exc:
        logger.warning("Skipping channel default_config for {}: {}", channel_cls, exc)
        return None
    if payload is None:
        return None
    if not isinstance(payload, dict):
        logger.warning(
            "Skipping channel default_config for {}: expected dict, got {}",
            channel_cls,
            type(payload).__name__,
        )
        return None
    return payload


def _onboard_plugins(config_path: Path) -> None:
    """Inject default config for all discovered channels (built-in + plugins)."""
    import json

    from hahobot.channels.registry import discover_all

    all_channels = discover_all()
    if not all_channels:
        return

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    channels = data.setdefault("channels", {})
    for name, cls in all_channels.items():
        payload = _resolve_channel_default_config(cls)
        if payload is None:
            continue
        if name not in channels:
            channels[name] = payload
        else:
            channels[name] = _merge_missing_defaults(channels[name], payload)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _make_single_provider(
    config: Config,
    *,
    model: str,
    provider_name: str | None = None,
):
    """Create one provider instance from config."""
    from hahobot.providers.base import GenerationSettings
    from hahobot.providers.registry import find_by_name

    resolved_provider_name = provider_name or config.get_provider_name(model)
    spec = find_by_name(resolved_provider_name) if resolved_provider_name else None
    if provider_name and spec is None:
        console.print(f"[red]Error: Unknown provider: {provider_name}[/red]")
        raise typer.Exit(1)

    if spec is not None:
        p = getattr(config.providers, spec.name, None)
    else:
        p = config.get_provider(model)
    backend = spec.backend if spec else "openai_compat"
    api_base = None
    if p and p.api_base:
        api_base = p.api_base
    elif spec and (spec.is_gateway or spec.is_local) and spec.default_api_base:
        api_base = spec.default_api_base

    # --- validation ---
    if backend == "azure_openai":
        if not p or not p.api_key or not p.api_base:
            console.print("[red]Error: Azure OpenAI requires api_key and api_base.[/red]")
            console.print("Set them in ~/.hahobot/config.json under providers.azure_openai section")
            console.print("Use the model field to specify the deployment name.")
            raise typer.Exit(1)
    elif backend == "openai_compat" and not model.startswith("bedrock/"):
        needs_key = not (p and p.api_key)
        exempt = spec and (spec.is_oauth or spec.is_local or spec.is_direct)
        if needs_key and not exempt:
            console.print("[red]Error: No API key configured.[/red]")
            console.print("Set one in ~/.hahobot/config.json under providers section")
            raise typer.Exit(1)

    # --- instantiation by backend ---
    if backend == "openai_codex":
        from hahobot.providers.openai_codex_provider import OpenAICodexProvider

        provider = OpenAICodexProvider(default_model=model)
    elif backend == "azure_openai":
        from hahobot.providers.azure_openai_provider import AzureOpenAIProvider

        provider = AzureOpenAIProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
        )
    elif backend == "github_copilot":
        from hahobot.providers.github_copilot_provider import GitHubCopilotProvider

        provider = GitHubCopilotProvider(default_model=model)
    elif backend == "anthropic":
        from hahobot.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=p.api_key if p else None,
            api_base=api_base,
            default_model=model,
            extra_headers=p.extra_headers if p else None,
        )
    else:
        from hahobot.providers.openai_compat_provider import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=p.api_key if p else None,
            api_base=api_base,
            default_model=model,
            extra_headers=p.extra_headers if p else None,
            spec=spec,
        )

    defaults = config.agents.defaults
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    return provider


def _make_provider(config: Config):
    """Create the configured LLM provider or provider pool from config."""
    defaults = config.agents.defaults
    provider_pool = defaults.provider_pool
    if provider_pool and provider_pool.targets:
        from hahobot.providers.pool_provider import ProviderPoolEntry, ProviderPoolProvider

        entries = [
            ProviderPoolEntry(
                name=target.provider,
                model=target.model,
                provider=_make_single_provider(
                    config,
                    model=target.model or defaults.model,
                    provider_name=target.provider,
                ),
            )
            for target in provider_pool.targets
        ]
        pooled = ProviderPoolProvider(
            entries,
            strategy=provider_pool.strategy,
            default_model=defaults.model,
        )
        pooled.generation = entries[0].provider.generation
        return pooled

    return _make_single_provider(config, model=defaults.model)


def _load_runtime_config(
    config: str | None = None,
    workspace: str | None = None,
    *,
    quiet: bool = False,
) -> Config:
    """Load config and optionally override the active workspace."""
    from hahobot.config.loader import load_config, resolve_config_env_vars, set_config_path

    config_path = None
    if config:
        config_path = Path(config).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        set_config_path(config_path)
        if not quiet:
            console.print(f"[dim]Using config: {config_path}[/dim]")

    try:
        loaded = resolve_config_env_vars(load_config(config_path))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    loaded.bind_config_path(config_path or loaded._config_path)
    _warn_deprecated_config_keys(config_path, quiet=quiet)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded


def _warn_deprecated_config_keys(config_path: Path | None, *, quiet: bool = False) -> None:
    """Hint users to remove obsolete keys from their config file."""
    import json

    from hahobot.config.loader import get_config_path

    path = config_path or get_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if "memoryWindow" in raw.get("agents", {}).get("defaults", {}):
        if not quiet:
            console.print(
                "[dim]Hint: `memoryWindow` in your config is no longer used "
                "and can be safely removed. Use `contextWindowTokens` to control "
                "prompt context size instead.[/dim]"
            )


def _migrate_cron_store(config: Config) -> None:
    """One-time migration: move legacy global cron store into the workspace."""
    from hahobot.config.paths import get_cron_dir

    legacy_path = get_cron_dir() / "jobs.json"
    new_path = config.workspace_path / "cron" / "jobs.json"
    if legacy_path.is_file() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.move(str(legacy_path), str(new_path))
