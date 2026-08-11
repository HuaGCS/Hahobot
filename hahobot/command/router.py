"""Minimal command routing table for slash commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any

from hahobot.bus.events import OutboundMessage

if TYPE_CHECKING:
    from hahobot.bus.events import InboundMessage
    from hahobot.session.manager import Session

Handler = Callable[["CommandContext"], Awaitable["OutboundMessage | None"]]


@dataclass
class CommandContext:
    """Everything a command handler needs to produce a response."""

    msg: InboundMessage
    session: Session | None
    key: str
    raw: str
    args: str = ""
    loop: Any = None


class CommandRouter:
    """Pure dict-based command dispatch.

    Three tiers checked in order:
      1. *priority* — exact-match commands handled before the dispatch lock
         (e.g. /stop, /restart).
      2. *exact* — exact-match commands handled inside the dispatch lock.
      3. *prefix* — longest-prefix-first match (e.g. "/team ").
      4. *interceptors* — fallback predicates (e.g. team-mode active check).
    """

    def __init__(self) -> None:
        self._priority: dict[str, Handler] = {}
        self._exact: dict[str, Handler] = {}
        self._prefix: list[tuple[str, Handler]] = []
        self._interceptors: list[Handler] = []

    def priority(self, cmd: str, handler: Handler) -> None:
        self._priority[cmd] = handler

    def exact(self, cmd: str, handler: Handler) -> None:
        self._exact[cmd] = handler

    def prefix(self, pfx: str, handler: Handler) -> None:
        self._prefix.append((pfx, handler))
        self._prefix.sort(key=lambda p: len(p[0]), reverse=True)

    def intercept(self, handler: Handler) -> None:
        self._interceptors.append(handler)

    def is_priority(self, text: str) -> bool:
        return text.strip().lower() in self._priority

    async def dispatch_priority(self, ctx: CommandContext) -> OutboundMessage | None:
        """Dispatch a priority command. Called from run() without the lock."""
        handler = self._priority.get(ctx.raw.lower())
        if handler:
            return await handler(ctx)
        return None

    async def dispatch(self, ctx: CommandContext) -> OutboundMessage | None:
        """Try handlers, then reject unknown slash commands before they reach the model."""
        cmd = ctx.raw.lower()

        if handler := self._exact.get(cmd):
            return await handler(ctx)

        for pfx, handler in self._prefix:
            if cmd.startswith(pfx):
                ctx.args = ctx.raw[len(pfx) :]
                return await handler(ctx)

        for interceptor in self._interceptors:
            result = await interceptor(ctx)
            if result is not None:
                return result

        if ctx.raw.startswith("/"):
            return self._invalid_command_response(ctx)
        return None

    def _invalid_command_response(self, ctx: CommandContext) -> OutboundMessage:
        from hahobot.agent.i18n import text

        entered = ctx.raw.split(maxsplit=1)[0]
        commands = self._registered_commands()
        command = commands.get(entered.lower())
        language = "en"
        if ctx.loop is not None:
            try:
                session = ctx.session or ctx.loop.sessions.get_or_create(ctx.key)
                language = ctx.loop._get_session_language(session)
            except (AttributeError, TypeError):
                pass

        if command is not None and not command[1]:
            content = text(
                language,
                "command_no_arguments",
                entered=entered,
                command=command[0],
            )
        else:
            matches = get_close_matches(entered.lower(), commands, n=1, cutoff=0.6)
            if matches:
                content = text(
                    language,
                    "unknown_command_suggestion",
                    entered=entered,
                    suggestion=commands[matches[0]][0],
                )
            else:
                content = text(language, "unknown_command", entered=entered)

        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=content,
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    def _registered_commands(self) -> dict[str, tuple[str, bool]]:
        """Return display form and argument support for every registered command."""
        commands: dict[str, tuple[str, bool]] = {
            command.lower(): (command, False)
            for command in (*self._priority, *self._exact)
            if command
        }
        for prefix, _handler in self._prefix:
            command = prefix.rstrip()
            if command:
                commands[command.lower()] = (command, True)
        return commands
