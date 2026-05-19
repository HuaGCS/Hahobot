"""Interactive CLI REPL infrastructure: completion, rendering, terminal I/O."""

from __future__ import annotations

import os
import select
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from hahobot import __logo__
from hahobot.cli.stream import ThinkingSpinner
from hahobot.command.catalog import interactive_command_names, interactive_subcommands


def _sanitize_surrogates(text: str) -> str:
    """Replace malformed surrogate code points while preserving valid pairs."""
    return text.encode("utf-16-le", errors="surrogatepass").decode(
        "utf-16-le",
        errors="replace",
    )


class SafeFileHistory(FileHistory):
    """FileHistory subclass that sanitizes surrogate characters on write.

    On Windows, special Unicode input (emoji, mixed-script) can produce
    surrogate characters that crash prompt_toolkit's file write.
    See issue #2846.
    """

    def store_string(self, string: str) -> None:
        super().store_string(_sanitize_surrogates(string))


console = Console()


EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}


_INTERACTIVE_SLASH_COMMANDS = interactive_command_names()


_INTERACTIVE_SLASH_SUBCOMMANDS: dict[str, tuple[str, ...]] = interactive_subcommands()


_INTERACTIVE_SCENE_NAMES = ("daily", "comfort", "date")


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
        if command == "/skill" and subcommand == "supersede":
            return ["remove", "clear"]
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
            session.key for session in list_session_summaries(manager, cli_only=True, limit=50)
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
    return "Local repo commands:\n/repo status\n/repo diff\n/repo diff staged"


def _interactive_review_usage() -> str:
    """Return help text for local interactive review controls."""
    return "Local review commands:\n/review\n/review staged"


def _interactive_compact_usage() -> str:
    """Return help text for local interactive compaction controls."""
    return "Local compaction commands:\n/compact\n/compact <key>"


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
        ansi = _render_interactive_ansi(lambda c: c.print(f"  [dim]↳ {text}[/dim]"))
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
        return _LocalInteractiveCommandResult(f"Exported session: {target}\nPath: {output_path}")

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
        str(item.get("key") or "") for item in loop.sessions.list_sessions() if item.get("key")
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
                    HTML(
                        "<style fg='ansigray'>Multiline mode: Enter newline, Ctrl+J submit</style>"
                    )
                    if multiline
                    else None
                ),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc
