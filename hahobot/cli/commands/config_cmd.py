"""``hahobot config`` — read/write per-skill config in ``<workspace>/skills.json``.

Mirrors the OpenClaw ``config set skills.entries.<name>.config.<key> <value>``
shape, but scoped to the ``skills.*`` namespace and persisted to a workspace-local
file so a skill's secrets never touch the process environment or other skills.
The file is the source of truth; a skill reads its own entry at runtime via
``read_file`` (``entries.<name>.config``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from hahobot.cli.commands import runtime
from hahobot.cli.commands._app import config_app
from hahobot.cli.commands.interactive import console

_MISSING = object()
_ROOT = "skills"  # only namespace routed to the workspace skills file
_FILE_NAME = "skills.json"


def _skills_file(config: str | None, workspace: str | None, *, quiet: bool = True) -> Path:
    """Resolve ``<workspace>/skills.json`` for the active config/workspace."""
    loaded = runtime._load_runtime_config(config, workspace, quiet=quiet)
    return loaded.workspace_path / _FILE_NAME


def _subpath(dotted: str) -> list[str]:
    """Split ``skills.entries.x.config.k`` into the in-file keys after ``skills.``."""
    parts = [p for p in dotted.split(".") if p]
    if not parts or parts[0] != _ROOT:
        console.print(
            f"[red]Error: only '{_ROOT}.*' paths are supported (got '{dotted or '<empty>'}').[/red]"
        )
        raise typer.Exit(1)
    return parts[1:]


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]Error: cannot read {path}: {exc}[/red]")
        raise typer.Exit(1) from None
    if not isinstance(data, dict):
        console.print(f"[red]Error: {path} is not a JSON object.[/red]")
        raise typer.Exit(1)
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _get_nested(root: dict[str, Any], keys: list[str]) -> Any:
    cur: Any = root
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return _MISSING
        cur = cur[key]
    return cur


def _set_nested(root: dict[str, Any], keys: list[str], value: Any) -> None:
    cur = root
    for key in keys[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[keys[-1]] = value


def _unset_nested(root: dict[str, Any], keys: list[str]) -> bool:
    cur: Any = root
    for key in keys[:-1]:
        if not isinstance(cur, dict) or key not in cur:
            return False
        cur = cur[key]
    if isinstance(cur, dict) and keys[-1] in cur:
        del cur[keys[-1]]
        return True
    return False


@config_app.command("set")
def config_set(
    path: str = typer.Argument(
        ..., help="Dotted path, e.g. skills.entries.today-task.config.authCode"
    ),
    value: str = typer.Argument(..., help="Value to store (a string unless --json is given)"),
    as_json: bool = typer.Option(
        False, "--json", help="Parse VALUE as JSON (for bools/numbers/objects)"
    ),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
):
    """Add or update a value under ``skills.*`` in ``<workspace>/skills.json``."""
    keys = _subpath(path)
    if not keys:
        console.print("[red]Error: a key after 'skills.' is required.[/red]")
        raise typer.Exit(1)

    stored: Any = value
    if as_json:
        try:
            stored = json.loads(value)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Error: --json given but VALUE is not valid JSON: {exc}[/red]")
            raise typer.Exit(1) from None

    file_path = _skills_file(config, workspace)
    data = _load(file_path)
    _set_nested(data, keys, stored)
    _write(file_path, data)
    console.print(f"[green]Set[/green] {path} [dim]in {file_path}[/dim]")


@config_app.command("get")
def config_get(
    path: str = typer.Argument(..., help="Dotted path, e.g. skills.entries.today-task.config"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
):
    """Read a value under ``skills.*`` from ``<workspace>/skills.json``."""
    keys = _subpath(path)
    file_path = _skills_file(config, workspace)
    data = _load(file_path)
    value = _get_nested(data, keys) if keys else data
    if value is _MISSING:
        console.print(f"[yellow]Not set:[/yellow] {path} [dim]({file_path})[/dim]")
        raise typer.Exit(1)
    if isinstance(value, str):
        typer.echo(value)
    else:
        typer.echo(json.dumps(value, indent=2, ensure_ascii=False))


@config_app.command("unset")
def config_unset(
    path: str = typer.Argument(..., help="Dotted path to remove"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
):
    """Remove a value under ``skills.*`` from ``<workspace>/skills.json``."""
    keys = _subpath(path)
    if not keys:
        console.print("[red]Error: a key after 'skills.' is required.[/red]")
        raise typer.Exit(1)
    file_path = _skills_file(config, workspace)
    data = _load(file_path)
    if _unset_nested(data, keys):
        _write(file_path, data)
        console.print(f"[green]Unset[/green] {path} [dim]in {file_path}[/dim]")
    else:
        console.print(f"[yellow]Not set:[/yellow] {path} [dim]({file_path})[/dim]")
        raise typer.Exit(1)
