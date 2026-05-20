"""Sub-command groups: sessions, repo, memory, persona, companion, channels, etc."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from hahobot import __logo__
from hahobot.cli.commands import runtime
from hahobot.cli.commands._app import (
    app,
    channels_app,
    companion_app,
    memory_index_app,
    persona_app,
    plugins_app,
    provider_app,
    repo_app,
    sessions_app,
)
from hahobot.cli.commands.interactive import console


@sessions_app.command("list")
def sessions_list(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    limit: int = typer.Option(20, "--limit", help="Maximum number of sessions to show"),
    cli_only: bool = typer.Option(False, "--cli-only", help="Only show local CLI sessions"),
    include_internal: bool = typer.Option(
        False, "--all", help="Include internal cron/api/system sessions"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """List recent saved sessions for the active workspace."""
    import json

    from hahobot.cli.session_inspector import list_session_summaries, render_session_list_text
    from hahobot.session.manager import SessionManager

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    manager = SessionManager(loaded.workspace_path)
    sessions = list_session_summaries(
        manager,
        include_internal=include_internal,
        cli_only=cli_only,
        limit=limit,
    )
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "workspace": str(loaded.workspace_path),
                    "count": len(sessions),
                    "cli_only": cli_only,
                    "include_internal": include_internal,
                    "sessions": [session.to_dict() for session in sessions],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    console.print(
        render_session_list_text(
            sessions,
            cli_only=cli_only,
            include_internal=include_internal,
        )
    )


@sessions_app.command("show")
def sessions_show(
    session_key: str = typer.Argument(..., help="Exact session key to inspect"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    limit: int = typer.Option(20, "--limit", help="Maximum number of recent messages to show"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Show one saved session with metadata and recent messages."""
    import json

    from hahobot.cli.session_inspector import load_session_detail, render_session_detail_text
    from hahobot.session.manager import SessionManager

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    manager = SessionManager(loaded.workspace_path)
    detail = load_session_detail(manager, session_key, limit=limit)
    if detail is None:
        console.print(f"[red]Error: Session not found: {session_key}[/red]")
        raise typer.Exit(1)
    if json_output:
        typer.echo(json.dumps(detail.to_dict(), ensure_ascii=False, indent=2))
        return
    console.print(render_session_detail_text(detail))


@sessions_app.command("export")
def sessions_export(
    session_key: str = typer.Argument(..., help="Exact session key to export"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    export_format: str = typer.Option("md", "--format", help="Export format: md or json"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file path"),
):
    """Export one saved session to a local artifact file."""
    from hahobot.cli.session_inspector import export_session_artifact, load_session_export
    from hahobot.session.manager import SessionManager

    normalized_format = export_format.strip().lower()
    if normalized_format not in {"md", "json"}:
        console.print("[red]Error: --format must be one of: md, json[/red]")
        raise typer.Exit(1)

    loaded = runtime._load_runtime_config(config, workspace)
    manager = SessionManager(loaded.workspace_path)
    export_data = load_session_export(manager, session_key)
    if export_data is None:
        console.print(f"[red]Error: Session not found: {session_key}[/red]")
        raise typer.Exit(1)

    target = export_session_artifact(
        export_data,
        workspace=loaded.workspace_path,
        export_format=normalized_format,
        output_path=Path(output) if output else None,
    )
    console.print(f"Exported session: {session_key}")
    console.print(f"Path: {target}")


@sessions_app.command("compact")
def sessions_compact(
    session_key: str = typer.Argument(..., help="Exact session key to compact"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Manually run session token consolidation for one saved session."""
    import json

    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus
    from hahobot.cli.session_compactor import compact_session, render_session_compact_text
    from hahobot.config.loader import get_config_path
    from hahobot.session.manager import SessionManager

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    manager = SessionManager(loaded.workspace_path)
    known_sessions = {
        str(item.get("key") or "") for item in manager.list_sessions() if item.get("key")
    }
    if session_key not in known_sessions:
        console.print(f"[red]Error: Session not found: {session_key}[/red]")
        raise typer.Exit(1)

    provider = runtime._make_provider(loaded)
    runtime_config_path = Path(config).expanduser().resolve() if config else get_config_path()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=loaded.workspace_path,
        config_path=runtime_config_path,
        model=loaded.agents.defaults.model,
        max_iterations=loaded.agents.defaults.max_tool_iterations,
        context_window_tokens=loaded.agents.defaults.context_window_tokens,
        context_block_limit=loaded.agents.defaults.context_block_limit,
        max_tool_result_chars=loaded.agents.defaults.max_tool_result_chars,
        provider_retry_mode=loaded.agents.defaults.provider_retry_mode,
        tool_hint_max_length=loaded.agents.defaults.tool_hint_max_length,
        web_config=loaded.tools.web,
        exec_config=loaded.tools.exec,
        image_gen_config=loaded.tools.image_gen,
        memory_config=loaded.memory,
        restrict_to_workspace=loaded.tools.restrict_to_workspace,
        session_manager=manager,
        mcp_servers=loaded.tools.mcp_servers,
        channels_config=loaded.channels,
        timezone=loaded.agents.defaults.timezone,
        unified_session=loaded.agents.defaults.unified_session,
        session_ttl_minutes=loaded.agents.defaults.session_ttl_minutes,
        disabled_skills=loaded.agents.defaults.disabled_skills,
    )

    async def _run_compaction() -> Any:
        try:
            session = manager.get_or_create(session_key)
            return await compact_session(session, loop)
        finally:
            await loop.close_mcp()

    report = asyncio.run(_run_compaction())
    if json_output:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    console.print(render_session_compact_text(report))


@memory_index_app.command("rebuild")
def memory_index_rebuild(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    persona: str | None = typer.Option(None, "--persona", help="Persona name to rebuild"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Rebuild the derived SQLite index for archived history."""
    import json

    from hahobot.agent.history_archive import HistoryArchiveStore

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    store = HistoryArchiveStore(loaded.workspace_path, persona=persona, index_backend="sqlite")
    count = store.rebuild_sqlite_index()
    payload = {
        "workspace": str(loaded.workspace_path),
        "persona": persona or "default",
        "archiveDir": str(store.archive_dir),
        "indexPath": str(store.archive_dir / "index.sqlite"),
        "count": count,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print("Rebuilt history archive SQLite index")
    console.print(f"Workspace: {payload['workspace']}")
    console.print(f"Persona: {payload['persona']}")
    console.print(f"Entries: {count}")
    console.print(f"Index: {payload['indexPath']}")


@repo_app.command("status")
def repo_status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Show read-only Git status for the active workspace."""
    import json

    from hahobot.cli.repo_inspector import inspect_repo_status, render_repo_status_text

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    summary = inspect_repo_status(loaded.workspace_path)
    if json_output:
        typer.echo(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        if not summary.is_git_repo:
            raise typer.Exit(1)
        return
    console.print(render_repo_status_text(summary))
    if not summary.is_git_repo:
        raise typer.Exit(1)


@repo_app.command("diff")
def repo_diff(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    staged: bool = typer.Option(False, "--staged", help="Inspect staged tracked changes"),
    name_only: bool = typer.Option(False, "--name-only", help="Only list changed paths"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Show a read-only tracked diff summary for the active workspace repository."""
    import json

    from hahobot.cli.repo_inspector import inspect_repo_diff, render_repo_diff_text

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    summary = inspect_repo_diff(loaded.workspace_path, staged=staged, name_only=name_only)
    if json_output:
        typer.echo(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        if not summary.is_git_repo:
            raise typer.Exit(1)
        return
    console.print(render_repo_diff_text(summary))
    if not summary.is_git_repo:
        raise typer.Exit(1)


@persona_app.command("import-st-card")
def persona_import_st_card(
    file: str = typer.Argument(..., help="Path to a SillyTavern character card JSON file"),
    name: str | None = typer.Option(None, "--name", help="Override the imported persona name"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite managed persona files if the target directory already exists",
    ),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Import a SillyTavern character card into `personas/<name>/`."""
    from hahobot.cli.persona_import import import_sillytavern_character_card

    loaded = runtime._load_runtime_config(config, workspace)
    loaded.workspace_path.mkdir(parents=True, exist_ok=True)
    runtime.sync_workspace_templates(loaded.workspace_path, silent=True)

    source = Path(file).expanduser().resolve()
    if not source.is_file():
        console.print(f"[red]Error: Character card file not found: {source}[/red]")
        raise typer.Exit(1)

    try:
        result = import_sillytavern_character_card(
            loaded.workspace_path,
            source,
            persona_name=name,
            force=force,
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc

    action = "Updated" if result.overwritten else "Imported"
    console.print(
        f"[green]✓[/green] {action} SillyTavern card '{result.display_name}' as persona "
        f"'{result.persona_name}'"
    )
    console.print(f"  Persona directory: [cyan]{result.persona_dir}[/cyan]")
    console.print(f"  Next step: use [cyan]/persona set {result.persona_name}[/cyan] in a session")


@persona_app.command("import-st-preset")
def persona_import_st_preset(
    file: str = typer.Argument(..., help="Path to a SillyTavern preset JSON file"),
    persona: str = typer.Option(..., "--persona", help="Target existing persona name"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite STYLE.md if it already exists for the target persona",
    ),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Import a SillyTavern preset into an existing persona as `STYLE.md`."""
    from hahobot.cli.persona_import import import_sillytavern_preset

    loaded = runtime._load_runtime_config(config, workspace)
    loaded.workspace_path.mkdir(parents=True, exist_ok=True)
    runtime.sync_workspace_templates(loaded.workspace_path, silent=True)

    source = Path(file).expanduser().resolve()
    if not source.is_file():
        console.print(f"[red]Error: Preset file not found: {source}[/red]")
        raise typer.Exit(1)

    try:
        result = import_sillytavern_preset(
            loaded.workspace_path,
            source,
            persona_name=persona,
            force=force,
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc

    action = "Updated" if result.overwritten else "Imported"
    console.print(
        f"[green]✓[/green] {action} SillyTavern preset into persona '{result.persona_name}'"
    )
    console.print(f"  Generated file: [cyan]{result.persona_dir / 'STYLE.md'}[/cyan]")


@persona_app.command("import-st-worldinfo")
def persona_import_st_worldinfo(
    file: str = typer.Argument(..., help="Path to a SillyTavern world info JSON file"),
    persona: str = typer.Option(..., "--persona", help="Target existing persona name"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite LORE.md if it already exists for the target persona",
    ),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Import SillyTavern world info into an existing persona as `LORE.md`."""
    from hahobot.cli.persona_import import import_sillytavern_world_info

    loaded = runtime._load_runtime_config(config, workspace)
    loaded.workspace_path.mkdir(parents=True, exist_ok=True)
    runtime.sync_workspace_templates(loaded.workspace_path, silent=True)

    source = Path(file).expanduser().resolve()
    if not source.is_file():
        console.print(f"[red]Error: World info file not found: {source}[/red]")
        raise typer.Exit(1)

    try:
        result = import_sillytavern_world_info(
            loaded.workspace_path,
            source,
            persona_name=persona,
            force=force,
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc

    action = "Updated" if result.overwritten else "Imported"
    console.print(
        f"[green]✓[/green] {action} SillyTavern world info into persona '{result.persona_name}'"
    )
    console.print(f"  Generated file: [cyan]{result.persona_dir / 'LORE.md'}[/cyan]")


@companion_app.command("doctor")
def companion_doctor(
    persona: str | None = typer.Option(None, "--persona", help="Target persona to inspect"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Run a read-only readiness check for companion-oriented workspace flows."""
    import json

    from hahobot.cli.companion_doctor import render_companion_doctor_text, run_companion_doctor

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    report = run_companion_doctor(loaded, persona=persona)
    if json_output:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    console.print(render_companion_doctor_text(report))


@companion_app.command("init")
def companion_init(
    persona: str | None = typer.Option(
        None,
        "--persona",
        help="Target persona name. Defaults to the workspace root persona.",
    ),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    force: bool = typer.Option(
        False, "--force", help="Overwrite managed companion files if they already exist"
    ),
    reference_image: str | None = typer.Option(
        None,
        "--reference-image",
        help="Optional image file to copy into persona assets and register as reference_image",
    ),
    no_heartbeat_task: bool = typer.Option(
        False,
        "--no-heartbeat-task",
        help="Do not inject the default companion heartbeat task",
    ),
):
    """Create a minimal companion persona scaffold in the active workspace."""
    from hahobot.cli.companion_init import init_companion_workspace

    loaded = runtime._load_runtime_config(config, workspace)
    try:
        result = init_companion_workspace(
            loaded,
            persona=persona,
            force=force,
            reference_image=reference_image,
            add_heartbeat_task=not no_heartbeat_task,
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] Companion scaffold ready for persona '{result.persona}'")
    console.print(f"  Workspace: [cyan]{result.workspace}[/cyan]")
    console.print(f"  Persona directory: [cyan]{result.persona_dir}[/cyan]")
    if result.created_paths:
        console.print("  Created:")
        for path in result.created_paths:
            console.print(f"    - [cyan]{path}[/cyan]")
    if result.updated_paths:
        console.print("  Updated:")
        for path in result.updated_paths:
            console.print(f"    - [cyan]{path}[/cyan]")
    if result.skipped_paths:
        console.print("  Kept existing:")
        for path in result.skipped_paths:
            console.print(f"    - [cyan]{path}[/cyan]")
    if result.copied_assets:
        console.print("  Copied assets:")
        for path in result.copied_assets:
            console.print(f"    - [cyan]{path}[/cyan]")
    console.print(
        f"  Next step: run [cyan]hahobot companion doctor --persona {result.persona}[/cyan]"
    )


@channels_app.command("status")
def channels_status(
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Show channel status."""
    from hahobot.channels.registry import discover_channel_names, load_channel_class
    from hahobot.config.loader import load_config, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)

    config = load_config(resolved_config_path)

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled")

    for modname in sorted(discover_channel_names()):
        section = getattr(config.channels, modname, None)
        enabled = section and getattr(section, "enabled", False)
        try:
            cls = load_channel_class(modname)
            display = cls.display_name
        except ImportError:
            display = modname.title()
        table.add_row(
            display,
            "[green]\u2713[/green]" if enabled else "[dim]\u2717[/dim]",
        )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    from hahobot.config.paths import get_bridge_install_dir

    user_bridge = get_bridge_install_dir()

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # hahobot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall hahobot")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1) from None

    return user_bridge


@channels_app.command("login")
def channels_login(
    channel_name: str = typer.Argument(..., help="Channel name (e.g. weixin, whatsapp)"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force re-authentication even if already logged in"
    ),
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Authenticate with a channel via QR code or other interactive login."""
    from hahobot.channels.registry import discover_all
    from hahobot.config.loader import load_config, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)

    config = load_config(resolved_config_path)
    channel_cfg = getattr(config.channels, channel_name, None) or {}

    # Validate channel exists
    all_channels = discover_all()
    if channel_name not in all_channels:
        available = ", ".join(all_channels.keys())
        console.print(f"[red]Unknown channel: {channel_name}[/red]  Available: {available}")
        raise typer.Exit(1)

    console.print(f"{__logo__} {all_channels[channel_name].display_name} Login\n")

    channel_cls = all_channels[channel_name]
    channel = channel_cls(channel_cfg, bus=None)

    success = asyncio.run(channel.login(force=force))

    if not success:
        raise typer.Exit(1)


@plugins_app.command("list")
def plugins_list():
    """List all discovered channels (built-in and plugins)."""
    from hahobot.channels.registry import discover_all, discover_channel_names
    from hahobot.config.loader import load_config

    config = load_config()
    builtin_names = set(discover_channel_names())
    all_channels = discover_all()

    table = Table(title="Channel Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Enabled")

    for name in sorted(all_channels):
        cls = all_channels[name]
        source = "builtin" if name in builtin_names else "plugin"
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            source,
            "[green]yes[/green]" if enabled else "[dim]no[/dim]",
        )

    console.print(table)


@app.command()
def status():
    """Show hahobot status."""
    from hahobot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} hahobot Status\n")

    console.print(
        f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}"
    )
    console.print(
        f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}"
    )

    if config_path.exists():
        from hahobot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(
                    f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}"
                )


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn

    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(
        ..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"
    ),
):
    """Authenticate with an OAuth provider."""
    from hahobot.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive

        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(
            f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]"
        )
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1) from None


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    try:
        from hahobot.providers.github_copilot_provider import login_github_copilot

        console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")
        token = login_github_copilot(
            print_fn=lambda s: console.print(s),
            prompt_fn=lambda s: typer.prompt(s),
        )
        account = token.account_id or "GitHub"
        console.print(f"[green]✓ Authenticated with GitHub Copilot[/green]  [dim]{account}[/dim]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1) from None
