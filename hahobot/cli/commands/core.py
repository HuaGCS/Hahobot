"""Top-level CLI commands: onboard, doctor, model, tools, review."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import typer

from hahobot import __logo__, __version__
from hahobot.cli.commands import interactive, runtime
from hahobot.cli.commands._app import app
from hahobot.cli.commands.interactive import console
from hahobot.cli.commands.runtime import _onboard_plugins
from hahobot.config.schema import Config


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} hahobot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(None, "--version", "-v", callback=version_callback, is_eager=True),
):
    """hahobot - Personal AI Assistant."""


@app.command()
def onboard(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    wizard: bool = typer.Option(False, "--wizard", help="Use interactive wizard"),
):
    """Initialize hahobot configuration and workspace."""
    from hahobot.config.loader import (
        find_compatible_config_source,
        get_config_path,
        load_config,
        save_config,
        set_config_path,
    )
    from hahobot.config.schema import Config

    compatible_source = None
    if config:
        config_path = Path(config).expanduser().resolve()
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")
    else:
        config_path = get_config_path()
        compatible_source = find_compatible_config_source()

    def _apply_workspace_override(loaded: Config) -> Config:
        if workspace:
            loaded.agents.defaults.workspace = workspace
        return loaded

    # Create or update config
    if compatible_source is not None and not config_path.exists():
        config = _apply_workspace_override(load_config())
        if not config_path.exists():
            save_config(config, config_path)
        console.print(
            f"[green]✓[/green] Copied legacy config from {compatible_source} to {config_path}"
        )
    elif config_path.exists():
        if wizard:
            config = _apply_workspace_override(load_config(config_path))
        else:
            console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
            console.print(
                "  [bold]y[/bold] = overwrite with defaults (existing values will be lost)"
            )
            console.print(
                "  [bold]N[/bold] = refresh config, keeping existing values and adding new fields"
            )
            if typer.confirm("Overwrite?"):
                config = _apply_workspace_override(Config())
                save_config(config, config_path)
                console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
            else:
                config = _apply_workspace_override(load_config(config_path))
                save_config(config, config_path)
                console.print(
                    f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)"
                )
    else:
        config = _apply_workspace_override(Config())
        # In wizard mode, don't save yet - the wizard will handle saving if should_save=True
        if not wizard:
            save_config(config, config_path)
            console.print(f"[green]✓[/green] Created config at {config_path}")

    # Run interactive wizard if enabled
    if wizard:
        from hahobot.cli.onboard import run_onboard

        try:
            result = run_onboard(initial_config=config)
            if not result.should_save:
                console.print("[yellow]Configuration discarded. No changes were saved.[/yellow]")
                return

            config = result.config
            save_config(config, config_path)
            console.print(f"[green]✓[/green] Config saved at {config_path}")
        except Exception as e:
            console.print(f"[red]✗[/red] Error during configuration: {e}")
            console.print("[yellow]Please run 'hahobot onboard' again to complete setup.[/yellow]")
            raise typer.Exit(1)
    _onboard_plugins(config_path)

    # Create workspace, preferring the configured workspace path.
    workspace_path = runtime.get_workspace_path(config.workspace_path)
    if not workspace_path.exists():
        workspace_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace_path}")

    runtime.sync_workspace_templates(workspace_path)

    agent_cmd = 'hahobot agent -m "Hello!"'
    gateway_cmd = "hahobot gateway"
    if config:
        agent_cmd += f" --config {config_path}"
        gateway_cmd += f" --config {config_path}"

    console.print(f"\n{__logo__} hahobot is ready!")
    console.print("\nNext steps:")
    if wizard:
        console.print(f"  1. Chat: [cyan]{agent_cmd}[/cyan]")
        console.print(f"  2. Start gateway: [cyan]{gateway_cmd}[/cyan]")
    else:
        console.print(f"  1. Add your API key to [cyan]{config_path}[/cyan]")
        console.print("     Get one at: https://openrouter.ai/keys")
        console.print(f"  2. Chat: [cyan]{agent_cmd}[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: README.md#-chat-apps[/dim]")


@app.command()
def doctor(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Run a read-only readiness check for the active runtime."""
    import json

    from hahobot.cli.runtime_doctor import render_runtime_doctor_text, run_runtime_doctor

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    report = run_runtime_doctor(loaded)
    if json_output:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    console.print(render_runtime_doctor_text(report))


@app.command()
def model(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Show the active model route and provider resolution."""
    import json

    from hahobot.cli.runtime_doctor import build_model_summary, render_model_summary_text

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    summary = build_model_summary(loaded)
    if json_output:
        typer.echo(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return
    console.print(render_model_summary_text(summary))


@app.command()
def tools(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Show the active tool configuration and readiness hints."""
    import json

    from hahobot.cli.runtime_doctor import build_tools_summary, render_tools_summary_text

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    summary = build_tools_summary(loaded)
    if json_output:
        typer.echo(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return
    console.print(render_tools_summary_text(summary))


@app.command()
def review(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    staged: bool = typer.Option(False, "--staged", help="Review staged tracked changes"),
    base: str | None = typer.Option(None, "--base", help="Review diff against a base revision"),
    path_filter: str | None = typer.Option(
        None,
        "--path",
        help="Limit review to one repository-relative path",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    markdown: bool = typer.Option(
        True,
        "--markdown/--no-markdown",
        help="Render review output as Markdown",
    ),
):
    """Review the active workspace Git diff with the configured model."""
    import json

    from hahobot.cli.review_runner import collect_review_input, run_review_for_input

    if staged and base:
        console.print("[red]Error: choose only one of --staged or --base.[/red]")
        raise typer.Exit(1)

    loaded = runtime._load_runtime_config(config, workspace, quiet=json_output)
    payload = collect_review_input(
        loaded.workspace_path,
        staged=staged,
        base=base,
        path_filter=path_filter,
    )

    if payload.error:
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "request": payload.to_dict(),
                        "model": loaded.agents.defaults.model,
                        "content": payload.error,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            console.print(f"[red]Error: {payload.error}[/red]")
        raise typer.Exit(1)

    if payload.clean:
        result = {
            "request": payload.to_dict(),
            "model": loaded.agents.defaults.model,
            "content": "No diff to review.",
        }
        if json_output:
            typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            interactive._print_agent_response("No diff to review.", render_markdown=markdown)
        return

    async def _run_review() -> Any:
        provider = runtime._make_provider(loaded)
        return await run_review_for_input(
            provider=provider,
            model=loaded.agents.defaults.model,
            payload=payload,
            retry_mode=loaded.agents.defaults.provider_retry_mode,
        )

    result = asyncio.run(_run_review())
    if json_output:
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        if result.request.error:
            raise typer.Exit(1)
        return

    if result.request.error:
        console.print(f"[red]Error: {result.content}[/red]")
        raise typer.Exit(1)

    interactive._print_agent_response(result.content, render_markdown=markdown)
