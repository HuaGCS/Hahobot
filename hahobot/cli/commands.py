"""CLI commands for hahobot."""

import asyncio
import os
import select
import signal
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # Re-open stdout/stderr with UTF-8 encoding
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import typer
from loguru import logger
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from hahobot import __logo__, __version__
from hahobot.cli.stream import StreamRenderer, ThinkingSpinner
from hahobot.command.catalog import interactive_command_names, interactive_subcommands
from hahobot.config.paths import get_workspace_path, is_default_workspace
from hahobot.config.schema import Config
from hahobot.utils.helpers import sync_workspace_templates
from hahobot.utils.restart import (
    consume_restart_notice_from_env,
    format_restart_completed_message,
    should_show_cli_restart_notice,
)


class SafeFileHistory(FileHistory):
    """FileHistory subclass that sanitizes surrogate characters on write.

    On Windows, special Unicode input (emoji, mixed-script) can produce
    surrogate characters that crash prompt_toolkit's file write.
    See issue #2846.
    """

    def store_string(self, string: str) -> None:
        safe = string.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
        super().store_string(safe)

app = typer.Typer(
    name="hahobot",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"{__logo__} hahobot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}
_INTERACTIVE_SLASH_COMMANDS = interactive_command_names()
_INTERACTIVE_SLASH_SUBCOMMANDS: dict[str, tuple[str, ...]] = interactive_subcommands()
_INTERACTIVE_SCENE_NAMES = ("daily", "comfort", "date")

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


@dataclass
class _InteractiveCompletionContext:
    """Runtime data used to enrich interactive slash completion."""

    workspace: Path | None = None
    session_manager: Any | None = None
    current_session_id: str | None = None


_INTERACTIVE_COMPLETION_CONTEXT = _InteractiveCompletionContext()


class _InteractiveSlashCompleter(Completer):
    """Context-aware slash-command completion for local interactive CLI input."""

    def get_completions(self, document, complete_event):
        del complete_event

        text = document.text_before_cursor
        if not text or not text.startswith("/") or text != text.lstrip():
            return
        if "\n" in text or "\r" in text:
            return

        has_trailing_space = text[-1].isspace()
        parts = text.split()
        if not parts:
            return

        if len(parts) == 1:
            if has_trailing_space:
                yield from self._complete_options(self._second_token_options(parts[0]), prefix="")
                return
            yield from self._complete_options(_INTERACTIVE_SLASH_COMMANDS, prefix=parts[0])
            return

        if len(parts) == 2:
            if has_trailing_space:
                yield from self._complete_options(
                    self._third_token_options(parts[0], parts[1]),
                    prefix="",
                )
                return
            yield from self._complete_options(self._second_token_options(parts[0]), prefix=parts[1])
            return

        if len(parts) != 3 or has_trailing_space:
            return

        yield from self._complete_options(
            self._third_token_options(parts[0], parts[1]),
            prefix=parts[2],
        )

    @staticmethod
    def _complete_options(options: list[str] | tuple[str, ...], *, prefix: str):
        start_position = -len(prefix)
        for option in options:
            if prefix and not option.startswith(prefix):
                continue
            yield Completion(option, start_position=start_position)

    def _second_token_options(self, command: str) -> list[str]:
        options = list(_INTERACTIVE_SLASH_SUBCOMMANDS.get(command, ()))
        if command == "/scene":
            options.extend(self._available_scene_names())
        if command == "/compact":
            options.extend(self._available_cli_sessions())
        return self._unique(options)

    def _third_token_options(self, command: str, subcommand: str) -> list[str]:
        if command in {"/lang", "/language"} and subcommand == "set":
            return self._available_languages()
        if command == "/persona" and subcommand == "set":
            return self._available_personas()
        if command == "/stchar" and subcommand in {"show", "load"}:
            return self._available_personas()
        if command == "/preset" and subcommand == "show":
            return self._available_personas()
        if command == "/session" and subcommand in {"show", "export", "use"}:
            return self._available_cli_sessions()
        if command == "/repo" and subcommand == "diff":
            return ["staged"]
        if command == "/review":
            return ["staged"]
        return []

    def _available_languages(self) -> list[str]:
        from hahobot.agent.i18n import list_languages

        return list_languages()

    def _available_personas(self) -> list[str]:
        from hahobot.agent.personas import list_personas

        workspace = _INTERACTIVE_COMPLETION_CONTEXT.workspace
        if workspace is None:
            return []
        return list_personas(workspace)

    def _available_scene_names(self) -> list[str]:
        from hahobot.agent.commands.scene import available_scene_names

        workspace = _INTERACTIVE_COMPLETION_CONTEXT.workspace
        if workspace is None:
            return list(_INTERACTIVE_SCENE_NAMES)
        return self._unique(
            [*_INTERACTIVE_SCENE_NAMES, *available_scene_names(workspace, self._current_persona())]
        )

    def _available_cli_sessions(self) -> list[str]:
        from hahobot.cli.session_inspector import list_session_summaries

        manager = _INTERACTIVE_COMPLETION_CONTEXT.session_manager
        if manager is None:
            return []
        options = []
        current_session_id = _INTERACTIVE_COMPLETION_CONTEXT.current_session_id
        if current_session_id:
            options.append(current_session_id)
        options.append("cli:direct")
        options.extend(
            session.key
            for session in list_session_summaries(manager, cli_only=True, limit=50)
        )
        return self._unique(options)

    def _current_persona(self) -> str | None:
        from hahobot.agent.personas import DEFAULT_PERSONA, resolve_persona_name

        workspace = _INTERACTIVE_COMPLETION_CONTEXT.workspace
        manager = _INTERACTIVE_COMPLETION_CONTEXT.session_manager
        current_session_id = _INTERACTIVE_COMPLETION_CONTEXT.current_session_id
        if manager is None or current_session_id is None:
            return DEFAULT_PERSONA
        session = manager.get_or_create(current_session_id)
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        raw = metadata.get("persona")
        if workspace is None:
            return raw or DEFAULT_PERSONA
        return resolve_persona_name(workspace, raw) or DEFAULT_PERSONA

    @staticmethod
    def _unique(options: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for option in options:
            if option in seen:
                continue
            seen.add(option)
            result.append(option)
        return result


_INTERACTIVE_SLASH_COMPLETER = _InteractiveSlashCompleter()


def _set_interactive_completion_context(
    *,
    workspace: Path | None,
    session_manager: Any | None,
    current_session_id: str | None,
) -> None:
    """Bind workspace/session context used by interactive slash completion."""
    _INTERACTIVE_COMPLETION_CONTEXT.workspace = workspace
    _INTERACTIVE_COMPLETION_CONTEXT.session_manager = session_manager
    _INTERACTIVE_COMPLETION_CONTEXT.current_session_id = current_session_id


def _update_interactive_completion_session(session_id: str) -> None:
    """Update the current session key for interactive slash completion."""
    _INTERACTIVE_COMPLETION_CONTEXT.current_session_id = session_id


def _clear_interactive_completion_context() -> None:
    """Drop interactive completion context after leaving CLI chat mode."""
    _set_interactive_completion_context(
        workspace=None,
        session_manager=None,
        current_session_id=None,
    )


@dataclass(frozen=True)
class _LocalInteractiveCommandResult:
    """Result of a local interactive command handled by the CLI shell."""

    text: str
    new_session_id: str | None = None


def _cli_route_for_session(session_id: str) -> tuple[str, str]:
    """Derive channel/chat identifiers for a CLI session key."""
    if ":" in session_id:
        return session_id.split(":", 1)
    return "cli", session_id


def _coerce_cli_session_key(value: str) -> str:
    """Normalize local CLI session references entered by the user."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("Session key cannot be empty.")
    if normalized == "direct":
        return "cli:direct"
    if ":" in normalized:
        return normalized
    return f"cli:{normalized}"


def _generate_new_cli_session_key(existing_keys: set[str]) -> str:
    """Generate a collision-resistant local CLI session key."""
    stem = datetime.now().strftime("cli:%Y%m%d-%H%M%S")
    if stem not in existing_keys:
        return stem
    for index in range(2, 1000):
        candidate = f"{stem}-{index}"
        if candidate not in existing_keys:
            return candidate
    return f"{stem}-{int(datetime.now().timestamp() * 1000)}"


def _interactive_session_usage() -> str:
    """Return help text for local interactive session controls."""
    return (
        "Local session commands:\n"
        "/session current\n"
        "/session list\n"
        "/session show [key]\n"
        "/session export [key]\n"
        "/session use <key>\n"
        "/session new [name]"
    )


def _interactive_repo_usage() -> str:
    """Return help text for local interactive repo inspection controls."""
    return (
        "Local repo commands:\n"
        "/repo status\n"
        "/repo diff\n"
        "/repo diff staged"
    )


def _interactive_review_usage() -> str:
    """Return help text for local interactive review controls."""
    return (
        "Local review commands:\n"
        "/review\n"
        "/review staged"
    )


def _interactive_compact_usage() -> str:
    """Return help text for local interactive compaction controls."""
    return (
        "Local compaction commands:\n"
        "/compact\n"
        "/compact <key>"
    )


def _interactive_key_bindings(*, multiline: bool) -> KeyBindings | None:
    """Return optional prompt_toolkit bindings for interactive CLI input."""
    if not multiline:
        return None

    bindings = KeyBindings()

    @bindings.add("c-j", eager=True)
    def _submit(event) -> None:
        event.current_buffer.validate_and_handle()

    return bindings


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    from hahobot.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=SafeFileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,  # Enter submits (single line mode)
    )


def _make_console() -> Console:
    return Console(file=sys.stdout)


def _render_interactive_ansi(render_fn) -> str:
    """Render Rich output to ANSI so prompt_toolkit can print it safely."""
    ansi_console = Console(
        force_terminal=True,
        color_system=console.color_system or "standard",
        width=console.width,
    )
    with ansi_console.capture() as capture:
        render_fn(ansi_console)
    return capture.get()


def _print_agent_response(
    response: str,
    render_markdown: bool,
    metadata: dict | None = None,
) -> None:
    """Render assistant response with consistent terminal styling."""
    console = _make_console()
    content = response or ""
    body = _response_renderable(content, render_markdown, metadata)
    console.print()
    console.print(f"[cyan]{__logo__} hahobot[/cyan]")
    console.print(body)
    console.print()


def _response_renderable(content: str, render_markdown: bool, metadata: dict | None = None):
    """Render plain-text command output without markdown collapsing newlines."""
    if not render_markdown:
        return Text(content)
    if (metadata or {}).get("render_as") == "text":
        return Text(content)
    return Markdown(content)


async def _print_interactive_line(text: str) -> None:
    """Print async interactive updates with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        ansi = _render_interactive_ansi(
            lambda c: c.print(f"  [dim]↳ {text}[/dim]")
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


async def _print_interactive_response(
    response: str,
    render_markdown: bool,
    metadata: dict | None = None,
) -> None:
    """Print async interactive replies with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        content = response or ""
        ansi = _render_interactive_ansi(
            lambda c: (
                c.print(),
                c.print(f"[cyan]{__logo__} hahobot[/cyan]"),
                c.print(_response_renderable(content, render_markdown, metadata)),
                c.print(),
            )
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


def _print_cli_progress_line(text: str, thinking: ThinkingSpinner | None) -> None:
    """Print a CLI progress line, pausing the spinner if needed."""
    with thinking.pause() if thinking else nullcontext():
        console.print(f"  [dim]↳ {text}[/dim]")


async def _print_interactive_progress_line(text: str, thinking: ThinkingSpinner | None) -> None:
    """Print an interactive progress line, pausing the spinner if needed."""
    with thinking.pause() if thinking else nullcontext():
        await _print_interactive_line(text)


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


def _select_session_interactively(sessions: list[Any]) -> str:
    """Prompt the user to choose a saved session or start a new one."""
    if not sessions:
        console.print("[dim]No saved CLI sessions found. Starting cli:direct.[/dim]")
        return "cli:direct"

    console.print("[cyan]Select a session to resume:[/cyan]")
    console.print("  0. Start a new session (cli:direct)")
    for index, session in enumerate(sessions, start=1):
        stamp = session.updated_at or session.created_at or "unknown-time"
        suffix = [stamp, f"{session.message_count} msg"]
        if session.persona:
            suffix.append(f"persona={session.persona}")
        console.print(f"  {index}. {session.key} ({', '.join(suffix)})")
        if session.preview:
            role = session.last_role or "message"
            console.print(f"     {role}: {session.preview}")

    while True:
        choice = typer.prompt("Selection", default="1").strip()
        if choice == "0":
            return "cli:direct"
        try:
            selected = int(choice)
        except ValueError:
            console.print("[yellow]Enter a number from the list above.[/yellow]")
            continue
        if 1 <= selected <= len(sessions):
            return sessions[selected - 1].key
        console.print("[yellow]Enter a valid session number.[/yellow]")


def _handle_local_session_command(
    command: str,
    *,
    session_manager: Any,
    current_session_id: str,
) -> _LocalInteractiveCommandResult:
    """Handle CLI-local `/session ...` commands without involving the agent."""
    from hahobot.cli.session_inspector import (
        export_session_artifact,
        list_session_summaries,
        load_session_detail,
        load_session_export,
        render_session_detail_text,
        render_session_list_text,
    )

    parts = command.strip().split(maxsplit=2)
    if len(parts) == 1:
        return _LocalInteractiveCommandResult(_interactive_session_usage())

    action = parts[1].lower()
    if action in {"current", "now"}:
        return _LocalInteractiveCommandResult(f"Current session: {current_session_id}")

    if action == "list":
        sessions = list_session_summaries(session_manager, cli_only=True, limit=20)
        return _LocalInteractiveCommandResult(render_session_list_text(sessions, cli_only=True))

    if action == "show":
        target = current_session_id if len(parts) < 3 else _coerce_cli_session_key(parts[2])
        detail = load_session_detail(session_manager, target, limit=10)
        if detail is None:
            return _LocalInteractiveCommandResult(
                f"Session not found: {target}\nUse /session new [name] to start a fresh session."
            )
        return _LocalInteractiveCommandResult(render_session_detail_text(detail))

    if action == "export":
        target = current_session_id if len(parts) < 3 else _coerce_cli_session_key(parts[2])
        export_data = load_session_export(session_manager, target)
        if export_data is None:
            return _LocalInteractiveCommandResult(
                f"Session not found: {target}\nUse /session list to inspect saved sessions."
            )
        output_path = export_session_artifact(
            export_data,
            workspace=session_manager.workspace,
            export_format="md",
        )
        return _LocalInteractiveCommandResult(
            f"Exported session: {target}\nPath: {output_path}"
        )

    existing = {
        session.key
        for session in list_session_summaries(session_manager, cli_only=True, limit=None)
    }

    if action == "use":
        if len(parts) < 3:
            return _LocalInteractiveCommandResult("Usage: /session use <key>")
        target = _coerce_cli_session_key(parts[2])
        if target == current_session_id:
            return _LocalInteractiveCommandResult(f"Already using session: {target}")
        if target != "cli:direct" and target not in existing:
            return _LocalInteractiveCommandResult(
                f"Session not found: {target}\nUse /session list to inspect saved sessions."
            )
        return _LocalInteractiveCommandResult(
            f"Switched to session: {target}",
            new_session_id=target,
        )

    if action == "new":
        target = (
            _generate_new_cli_session_key(existing)
            if len(parts) < 3 or not parts[2].strip()
            else _coerce_cli_session_key(parts[2])
        )
        if target in existing:
            return _LocalInteractiveCommandResult(
                f"Session already exists: {target}\nUse /session use {target} to resume it."
            )
        return _LocalInteractiveCommandResult(
            f"Started new session: {target}",
            new_session_id=target,
        )

    return _LocalInteractiveCommandResult(_interactive_session_usage())


def _handle_local_repo_command(
    command: str,
    *,
    workspace: Path,
) -> _LocalInteractiveCommandResult:
    """Handle CLI-local `/repo ...` commands without involving the agent."""
    from hahobot.cli.repo_inspector import (
        inspect_repo_diff,
        inspect_repo_status,
        render_repo_diff_text,
        render_repo_status_text,
    )

    parts = command.strip().split()
    if len(parts) == 1:
        return _LocalInteractiveCommandResult(_interactive_repo_usage())

    action = parts[1].lower()
    if action == "status":
        return _LocalInteractiveCommandResult(
            render_repo_status_text(inspect_repo_status(workspace))
        )

    if action == "diff":
        staged = len(parts) >= 3 and parts[2].lower() in {"staged", "cached"}
        if len(parts) >= 3 and not staged:
            return _LocalInteractiveCommandResult(_interactive_repo_usage())
        return _LocalInteractiveCommandResult(
            render_repo_diff_text(inspect_repo_diff(workspace, staged=staged))
        )

    return _LocalInteractiveCommandResult(_interactive_repo_usage())


async def _handle_local_review_command(
    command: str,
    *,
    provider: Any,
    model: str,
    workspace: Path,
    retry_mode: str = "standard",
) -> _LocalInteractiveCommandResult:
    """Handle CLI-local `/review ...` commands without involving the agent loop."""
    from hahobot.cli.review_runner import run_review

    parts = command.strip().split(maxsplit=1)
    if len(parts) == 1:
        result = await run_review(
            provider=provider,
            model=model,
            workspace=workspace,
            retry_mode=retry_mode,
        )
        return _LocalInteractiveCommandResult(result.content)

    arg = parts[1].strip().lower()
    if arg == "staged":
        result = await run_review(
            provider=provider,
            model=model,
            workspace=workspace,
            staged=True,
            retry_mode=retry_mode,
        )
        return _LocalInteractiveCommandResult(result.content)

    return _LocalInteractiveCommandResult(_interactive_review_usage())


async def _handle_local_compact_command(
    command: str,
    *,
    loop: Any,
    current_session_id: str,
) -> _LocalInteractiveCommandResult:
    """Handle CLI-local `/compact ...` commands without involving the agent loop."""
    from hahobot.cli.session_compactor import compact_session, render_session_compact_text

    parts = command.strip().split(maxsplit=1)
    if len(parts) == 1:
        target = current_session_id
    elif parts[1].strip():
        target = _coerce_cli_session_key(parts[1])
    else:
        return _LocalInteractiveCommandResult(_interactive_compact_usage())

    known_sessions = {
        str(item.get("key") or "")
        for item in loop.sessions.list_sessions()
        if item.get("key")
    }
    if target != current_session_id and target not in known_sessions:
        return _LocalInteractiveCommandResult(
            f"Session not found: {target}\nUse /session list to inspect saved sessions."
        )

    session = loop.sessions.get_or_create(target)
    report = await compact_session(session, loop)
    return _LocalInteractiveCommandResult(render_session_compact_text(report))


async def _read_interactive_input_async(*, multiline: bool = False) -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
                multiline=multiline,
                completer=_INTERACTIVE_SLASH_COMPLETER,
                key_bindings=_interactive_key_bindings(multiline=multiline),
                prompt_continuation=HTML("<b fg='ansiblue'>...</b> "),
                bottom_toolbar=(
                    HTML("<style fg='ansigray'>Multiline mode: Enter newline, Ctrl+J submit</style>")
                    if multiline
                    else None
                ),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} hahobot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """hahobot - Personal AI Assistant."""
    pass


persona_app = typer.Typer(help="Manage personas")
app.add_typer(persona_app, name="persona")

companion_app = typer.Typer(help="Companion workflow utilities")
app.add_typer(companion_app, name="companion")

sessions_app = typer.Typer(help="Inspect saved sessions")
app.add_typer(sessions_app, name="sessions")

repo_app = typer.Typer(help="Inspect local repository state")
app.add_typer(repo_app, name="repo")


# ============================================================================
# Onboard / Setup
# ============================================================================


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
    workspace_path = get_workspace_path(config.workspace_path)
    if not workspace_path.exists():
        workspace_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace_path}")

    sync_workspace_templates(workspace_path)

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
    console.print(
        "\n[dim]Want Telegram/WhatsApp? See: README.md#-chat-apps[/dim]"
    )


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
    from loguru import logger

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


def _migrate_cron_store(config: "Config") -> None:
    """One-time migration: move legacy global cron store into the workspace."""
    from hahobot.config.paths import get_cron_dir

    legacy_path = get_cron_dir() / "jobs.json"
    new_path = config.workspace_path / "cron" / "jobs.json"
    if legacy_path.is_file() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.move(str(legacy_path), str(new_path))


# ============================================================================
# Read-Only Runtime Diagnostics
# ============================================================================


@app.command()
def doctor(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Run a read-only readiness check for the active runtime."""
    import json

    from hahobot.cli.runtime_doctor import render_runtime_doctor_text, run_runtime_doctor

    loaded = _load_runtime_config(config, workspace, quiet=json_output)
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

    loaded = _load_runtime_config(config, workspace, quiet=json_output)
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

    loaded = _load_runtime_config(config, workspace, quiet=json_output)
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

    loaded = _load_runtime_config(config, workspace, quiet=json_output)
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
        result = {"request": payload.to_dict(), "model": loaded.agents.defaults.model, "content": "No diff to review."}
        if json_output:
            typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_agent_response("No diff to review.", render_markdown=markdown)
        return

    async def _run_review() -> Any:
        provider = _make_provider(loaded)
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

    _print_agent_response(result.content, render_markdown=markdown)


# ============================================================================
# OpenAI-Compatible API Server
# ============================================================================


@app.command()
def serve(
    port: int | None = typer.Option(None, "--port", "-p", help="API server port"),
    host: str | None = typer.Option(None, "--host", "-H", help="Bind address"),
    timeout: float | None = typer.Option(None, "--timeout", "-t", help="Per-request timeout (seconds)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show hahobot runtime logs"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the OpenAI-compatible API server (/v1/chat/completions)."""
    try:
        from aiohttp import web  # noqa: F401
    except ImportError:
        console.print("[red]aiohttp is required. Install with: pip install -e '.[api]'[/red]")
        raise typer.Exit(1)

    from loguru import logger

    from hahobot.agent.loop import AgentLoop
    from hahobot.api.server import create_app
    from hahobot.bus.queue import MessageBus
    from hahobot.session.manager import SessionManager

    if verbose:
        logger.enable("hahobot")
    else:
        logger.disable("hahobot")

    runtime_config = _load_runtime_config(config, workspace)
    api_cfg = runtime_config.api
    host = host if host is not None else api_cfg.host
    port = port if port is not None else api_cfg.port
    timeout = timeout if timeout is not None else api_cfg.timeout
    sync_workspace_templates(runtime_config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(runtime_config)
    session_manager = SessionManager(runtime_config.workspace_path)
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=runtime_config.workspace_path,
        model=runtime_config.agents.defaults.model,
        max_iterations=runtime_config.agents.defaults.max_tool_iterations,
        context_window_tokens=runtime_config.agents.defaults.context_window_tokens,
        context_block_limit=runtime_config.agents.defaults.context_block_limit,
        max_tool_result_chars=runtime_config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=runtime_config.agents.defaults.provider_retry_mode,
        web_config=runtime_config.tools.web,
        exec_config=runtime_config.tools.exec,
        restrict_to_workspace=runtime_config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=runtime_config.tools.mcp_servers,
        channels_config=runtime_config.channels,
        timezone=runtime_config.agents.defaults.timezone,
        unified_session=runtime_config.agents.defaults.unified_session,
        session_ttl_minutes=runtime_config.agents.defaults.session_ttl_minutes,
        disabled_skills=runtime_config.agents.defaults.disabled_skills,
    )

    model_name = runtime_config.agents.defaults.model
    console.print(f"{__logo__} Starting OpenAI-compatible API server")
    console.print(f"  [cyan]Endpoint[/cyan] : http://{host}:{port}/v1/chat/completions")
    console.print(f"  [cyan]Model[/cyan]    : {model_name}")
    console.print("  [cyan]Session[/cyan]  : api:default")
    console.print(f"  [cyan]Timeout[/cyan]  : {timeout}s")
    if host in {"0.0.0.0", "::"}:
        console.print(
            "[yellow]Warning:[/yellow] API is bound to all interfaces. "
            "Only do this behind a trusted network boundary, firewall, or reverse proxy."
        )
    console.print()

    api_app = create_app(agent_loop, model_name=model_name, request_timeout=timeout)

    async def on_startup(_app):
        await agent_loop._connect_mcp()

    async def on_cleanup(_app):
        await agent_loop.close_mcp()

    api_app.on_startup.append(on_startup)
    api_app.on_cleanup.append(on_cleanup)

    web.run_app(api_app, host=host, port=port, print=lambda msg: logger.info(msg))


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int | None = typer.Option(None, "--port", "-p", help="Gateway port"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the hahobot gateway."""
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus
    from hahobot.channels.manager import ChannelManager
    from hahobot.config.loader import get_config_path, load_config
    from hahobot.cron.service import CronService
    from hahobot.cron.types import CronJob
    from hahobot.gateway.http import GatewayHttpServer
    from hahobot.gateway.runtime_status import GatewayRuntimeStatusTracker, GatewayStatusHook
    from hahobot.heartbeat.service import HeartbeatService
    from hahobot.session.manager import SessionManager
    from hahobot.star_office import StarOfficeHook, StarOfficePushSettings, StarOfficeStatusTracker

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    config_arg = config
    config = _load_runtime_config(config_arg, workspace)
    runtime_config_path = Path(config_arg).expanduser().resolve() if config_arg else get_config_path()
    port = port if port is not None else config.gateway.port

    console.print(f"{__logo__} Starting hahobot gateway version {__version__} on port {port}...")
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)
    star_office_tracker = StarOfficeStatusTracker(
        push_settings=StarOfficePushSettings.from_status_config(config.gateway.status)
    )
    runtime_status_tracker = GatewayRuntimeStatusTracker(model=config.agents.defaults.model)

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path, max_sleep_ms=config.gateway.cron.max_sleep_ms)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        config_path=runtime_config_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        brave_api_key=config.tools.web.search.api_key or None,
        web_proxy=config.tools.web.proxy or None,
        web_search_provider=config.tools.web.search.provider,
        web_search_base_url=config.tools.web.search.base_url or None,
        web_search_max_results=config.tools.web.search.max_results,
        exec_config=config.tools.exec,
        image_gen_config=config.tools.image_gen,
        memory_config=config.memory,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=config.agents.defaults.timezone,
        hooks=[StarOfficeHook(star_office_tracker), GatewayStatusHook(runtime_status_tracker)],
        unified_session=config.agents.defaults.unified_session,
        session_ttl_minutes=config.agents.defaults.session_ttl_minutes,
        disabled_skills=config.agents.defaults.disabled_skills,
    )
    runtime_status_tracker.set_model(agent.model)

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        # Dream is an internal job — run directly, not through the agent loop.
        if job.name == "dream":
            try:
                await agent.dream.run()
                logger.info("Dream cron job completed")
            except Exception:
                logger.exception("Dream cron job failed")
            return None

        from hahobot.agent.tools.cron import CronTool
        from hahobot.agent.tools.message import MessageTool
        from hahobot.utils.evaluator import evaluate_response
        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        # Prevent the agent from scheduling new cron jobs during execution
        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            resp = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        response = resp.content if resp else ""

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            should_notify = await evaluate_response(
                response,
                reminder_note,
                provider,
                agent.model,
            )
            if should_notify:
                from hahobot.bus.events import OutboundMessage

                await bus.publish_outbound(
                    OutboundMessage(
                        channel=job.payload.channel or "cli",
                        chat_id=job.payload.to,
                        content=response,
                    )
                )
        return response

    cron.on_job = on_cron_job

    # Create channel manager
    channels = ChannelManager(config, bus)

    async def _reload_runtime_state() -> None:
        """Force-reload runtime-configurable state after admin config saves."""
        reloaded = load_config(runtime_config_path)
        sync_workspace_templates(reloaded.workspace_path, silent=True)
        cron.rebind_store(reloaded.workspace_path / "cron" / "jobs.json")
        cron.apply_runtime_config(reloaded.gateway.cron.max_sleep_ms)
        await agent.reload_runtime_config(reloaded)
        runtime_status_tracker.set_model(agent.model)
        channels.apply_runtime_config(reloaded)
        star_office_tracker.apply_push_settings(StarOfficePushSettings.from_status_config(reloaded.gateway.status))
        await heartbeat.apply_runtime_config(
            workspace=reloaded.workspace_path,
            model=agent.model,
            interval_s=reloaded.gateway.heartbeat.interval_s,
            enabled=reloaded.gateway.heartbeat.enabled,
            timezone=reloaded.agents.defaults.timezone,
        )
        http_server.update_runtime_workspace(reloaded.workspace_path)

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        resp = await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

        # Keep a small tail of heartbeat history so the loop stays bounded
        # without losing all short-term context between runs.
        session = agent.sessions.get_or_create("heartbeat")
        session.retain_recent_legal_suffix(hb_cfg.keep_recent_messages)
        agent.sessions.save(session)

        return resp.content if resp else ""

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from hahobot.bus.events import OutboundMessage
        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
        timezone=config.agents.defaults.timezone,
    )

    http_server = GatewayHttpServer(
        config.gateway.host,
        port,
        config_path=runtime_config_path,
        workspace=config.workspace_path,
        reload_runtime=_reload_runtime_state,
        star_office_tracker=star_office_tracker,
        runtime_status_tracker=runtime_status_tracker,
        heartbeat_service=heartbeat,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    # Register Dream system job (always-on, idempotent on restart)
    dream_cfg = config.agents.defaults.dream
    if dream_cfg.model_override:
        agent.dream.model = dream_cfg.model_override
    agent.dream.max_batch_size = dream_cfg.max_batch_size
    agent.dream.max_iterations = dream_cfg.max_iterations
    from hahobot.cron.types import CronJob, CronPayload
    cron.register_system_job(CronJob(
        id="dream",
        name="dream",
        schedule=dream_cfg.build_schedule(config.agents.defaults.timezone),
        payload=CronPayload(kind="system_event"),
    ))
    console.print(f"[green]✓[/green] Dream: {dream_cfg.describe_schedule()}")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            await http_server.start()
            star_office_tracker.publish_current()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await http_server.stop()
            await channels.stop_all()
            await star_office_tracker.aclose()

    asyncio.run(run())


# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str | None = typer.Option(None, "--session", "-s", help="Session ID"),
    continue_last: bool = typer.Option(False, "--continue", help="Resume the most recent CLI session"),
    pick_session: bool = typer.Option(False, "--pick-session", help="Interactively choose a recent CLI session"),
    multiline: bool = typer.Option(
        False,
        "--multiline",
        help="Enable multiline input in interactive mode (Enter newline, Ctrl+J submit)",
    ),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show hahobot runtime logs during chat"),
):
    """Interact with the agent directly."""
    from loguru import logger

    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus
    from hahobot.cli.session_inspector import list_session_summaries, pick_recent_cli_session_key
    from hahobot.config.loader import get_config_path
    from hahobot.cron.service import CronService
    from hahobot.session.manager import SessionManager

    config_arg = config
    config = _load_runtime_config(config_arg, workspace)
    runtime_config_path = Path(config_arg).expanduser().resolve() if config_arg else get_config_path()
    sync_workspace_templates(config.workspace_path)

    session_manager = SessionManager(config.workspace_path)
    if sum([session_id is not None, continue_last, pick_session]) > 1:
        console.print("[red]Error: choose only one of --session, --continue, or --pick-session.[/red]")
        raise typer.Exit(1)
    if session_id is None:
        if continue_last:
            session_id = pick_recent_cli_session_key(session_manager)
            if session_id is not None:
                console.print(f"[dim]Resuming session: {session_id}[/dim]")
            else:
                session_id = "cli:direct"
                console.print("[dim]No previous CLI session found. Starting cli:direct.[/dim]")
        elif pick_session:
            sessions = list_session_summaries(session_manager, cli_only=True, limit=20)
            session_id = _select_session_interactively(sessions)
            if session_id == "cli:direct":
                console.print("[dim]Starting new session: cli:direct[/dim]")
            else:
                console.print(f"[dim]Selected session: {session_id}[/dim]")
        else:
            session_id = "cli:direct"

    bus = MessageBus()
    provider = _make_provider(config)

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path, max_sleep_ms=config.gateway.cron.max_sleep_ms)

    if logs:
        logger.enable("hahobot")
    else:
        logger.disable("hahobot")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        config_path=runtime_config_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        brave_api_key=config.tools.web.search.api_key or None,
        web_proxy=config.tools.web.proxy or None,
        web_search_provider=config.tools.web.search.provider,
        web_search_base_url=config.tools.web.search.base_url or None,
        web_search_max_results=config.tools.web.search.max_results,
        exec_config=config.tools.exec,
        image_gen_config=config.tools.image_gen,
        memory_config=config.memory,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=config.agents.defaults.timezone,
        unified_session=config.agents.defaults.unified_session,
        session_ttl_minutes=config.agents.defaults.session_ttl_minutes,
        disabled_skills=config.agents.defaults.disabled_skills,
    )
    restart_notice = consume_restart_notice_from_env()
    if restart_notice and should_show_cli_restart_notice(restart_notice, session_id):
        _print_agent_response(
            format_restart_completed_message(restart_notice.started_at_raw),
            render_markdown=False,
        )

    # Shared reference for progress callbacks
    _thinking: ThinkingSpinner | None = None

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        _print_cli_progress_line(content, _thinking)

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            renderer = StreamRenderer(render_markdown=markdown)
            response = await agent_loop.process_direct(
                message, session_id,
                on_progress=_cli_progress,
                on_stream=renderer.on_delta,
                on_stream_end=renderer.on_end,
            )
            if not renderer.streamed:
                await renderer.close()
                _print_agent_response(
                    response.content if response else "",
                    render_markdown=markdown,
                    metadata=response.metadata if response else None,
                )
            await agent_loop.close_mcp()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from hahobot.bus.events import InboundMessage

        _set_interactive_completion_context(
            workspace=config.workspace_path,
            session_manager=session_manager,
            current_session_id=session_id,
        )
        _init_prompt_session()
        console.print(
            f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)"
        )
        if multiline:
            console.print("[dim]Multiline input enabled: Enter inserts newline, Ctrl+J submits.[/dim]")
        console.print()

        cli_channel, cli_chat_id = _cli_route_for_session(session_id)

        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            _restore_terminal()
            console.print(f"\nReceived {sig_name}, goodbye!")
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        # SIGHUP is not available on Windows
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, _handle_signal)
        # Ignore SIGPIPE to prevent silent process termination when writing to closed pipes
        # SIGPIPE is not available on Windows
        if hasattr(signal, 'SIGPIPE'):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[tuple[str, dict]] = []
            renderer: StreamRenderer | None = None
            state = {
                "session_id": session_id,
                "cli_channel": cli_channel,
                "cli_chat_id": cli_chat_id,
            }

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

                        if msg.metadata.get("_stream_delta"):
                            if renderer:
                                await renderer.on_delta(msg.content)
                            continue
                        if msg.metadata.get("_stream_end"):
                            if renderer:
                                await renderer.on_end(
                                    resuming=msg.metadata.get("_resuming", False),
                                )
                            continue
                        if msg.metadata.get("_streamed"):
                            turn_done.set()
                            continue

                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                await _print_interactive_progress_line(msg.content, _thinking)
                            continue

                        if not turn_done.is_set():
                            if msg.content:
                                turn_response.append((msg.content, dict(msg.metadata or {})))
                            turn_done.set()
                        elif msg.content:
                            await _print_interactive_response(
                                msg.content,
                                render_markdown=markdown,
                                metadata=msg.metadata,
                            )

                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        # Stop spinner before user input to avoid prompt_toolkit conflicts
                        if renderer:
                            renderer.stop_for_input()
                        user_input = await _read_interactive_input_async(multiline=multiline)
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        if command.startswith("/session"):
                            result = _handle_local_session_command(
                                command,
                                session_manager=session_manager,
                                current_session_id=str(state["session_id"]),
                            )
                            if result.new_session_id is not None:
                                state["session_id"] = result.new_session_id
                                _update_interactive_completion_session(result.new_session_id)
                                next_channel, next_chat_id = _cli_route_for_session(result.new_session_id)
                                state["cli_channel"] = next_channel
                                state["cli_chat_id"] = next_chat_id
                            await _print_interactive_response(
                                result.text,
                                render_markdown=False,
                                metadata={"render_as": "text"},
                            )
                            continue

                        if command.startswith("/repo"):
                            result = _handle_local_repo_command(
                                command,
                                workspace=config.workspace_path,
                            )
                            await _print_interactive_response(
                                result.text,
                                render_markdown=False,
                                metadata={"render_as": "text"},
                            )
                            continue

                        if command.startswith("/review"):
                            result = await _handle_local_review_command(
                                command,
                                provider=provider,
                                model=config.agents.defaults.model,
                                workspace=config.workspace_path,
                                retry_mode=config.agents.defaults.provider_retry_mode,
                            )
                            await _print_interactive_response(
                                result.text,
                                render_markdown=markdown,
                            )
                            continue

                        if command.startswith("/compact"):
                            result = await _handle_local_compact_command(
                                command,
                                loop=agent_loop,
                                current_session_id=str(state["session_id"]),
                            )
                            await _print_interactive_response(
                                result.text,
                                render_markdown=False,
                                metadata={"render_as": "text"},
                            )
                            continue

                        turn_done.clear()
                        turn_response.clear()
                        renderer = StreamRenderer(render_markdown=markdown)

                        await bus.publish_inbound(InboundMessage(
                            channel=str(state["cli_channel"]),
                            sender_id="user",
                            chat_id=str(state["cli_chat_id"]),
                            content=user_input,
                            metadata={"_wants_stream": True},
                            session_key_override=str(state["session_id"]),
                        ))

                        await turn_done.wait()

                        if turn_response:
                            content, meta = turn_response[0]
                            if content and not meta.get("_streamed"):
                                if renderer:
                                    await renderer.close()
                                _print_agent_response(
                                    content, render_markdown=markdown, metadata=meta,
                                )
                        elif renderer and not renderer.streamed:
                            await renderer.close()
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                _clear_interactive_completion_context()
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()

        asyncio.run(run_interactive())


@sessions_app.command("list")
def sessions_list(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    limit: int = typer.Option(20, "--limit", help="Maximum number of sessions to show"),
    cli_only: bool = typer.Option(False, "--cli-only", help="Only show local CLI sessions"),
    include_internal: bool = typer.Option(False, "--all", help="Include internal cron/api/system sessions"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """List recent saved sessions for the active workspace."""
    import json

    from hahobot.cli.session_inspector import list_session_summaries, render_session_list_text
    from hahobot.session.manager import SessionManager

    loaded = _load_runtime_config(config, workspace, quiet=json_output)
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

    loaded = _load_runtime_config(config, workspace, quiet=json_output)
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

    loaded = _load_runtime_config(config, workspace)
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

    loaded = _load_runtime_config(config, workspace, quiet=json_output)
    manager = SessionManager(loaded.workspace_path)
    known_sessions = {
        str(item.get("key") or "")
        for item in manager.list_sessions()
        if item.get("key")
    }
    if session_key not in known_sessions:
        console.print(f"[red]Error: Session not found: {session_key}[/red]")
        raise typer.Exit(1)

    provider = _make_provider(loaded)
    runtime_config_path = (
        Path(config).expanduser().resolve() if config else get_config_path()
    )
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


@repo_app.command("status")
def repo_status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Show read-only Git status for the active workspace."""
    import json

    from hahobot.cli.repo_inspector import inspect_repo_status, render_repo_status_text

    loaded = _load_runtime_config(config, workspace, quiet=json_output)
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

    loaded = _load_runtime_config(config, workspace, quiet=json_output)
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

    loaded = _load_runtime_config(config, workspace)
    loaded.workspace_path.mkdir(parents=True, exist_ok=True)
    sync_workspace_templates(loaded.workspace_path, silent=True)

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

    loaded = _load_runtime_config(config, workspace)
    loaded.workspace_path.mkdir(parents=True, exist_ok=True)
    sync_workspace_templates(loaded.workspace_path, silent=True)

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

    loaded = _load_runtime_config(config, workspace)
    loaded.workspace_path.mkdir(parents=True, exist_ok=True)
    sync_workspace_templates(loaded.workspace_path, silent=True)

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

    loaded = _load_runtime_config(config, workspace, quiet=json_output)
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
    force: bool = typer.Option(False, "--force", help="Overwrite managed companion files if they already exist"),
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

    loaded = _load_runtime_config(config, workspace)
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

    console.print(
        f"[green]✓[/green] Companion scaffold ready for persona '{result.persona}'"
    )
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
        "  Next step: run "
        f"[cyan]hahobot companion doctor --persona {result.persona}[/cyan]"
    )


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


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
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login(
    channel_name: str = typer.Argument(..., help="Channel name (e.g. weixin, whatsapp)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-authentication even if already logged in"),
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


# ============================================================================
# Plugin Commands
# ============================================================================

plugins_app = typer.Typer(help="Manage channel plugins")
app.add_typer(plugins_app, name="plugins")


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


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show hahobot status."""
    from hahobot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} hahobot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

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
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn

    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
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
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


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
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
