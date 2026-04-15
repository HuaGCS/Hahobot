"""Session routing and slash-command dispatch helpers for AgentLoop."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from hahobot.agent.i18n import text
from hahobot.command.router import CommandContext

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.events import InboundMessage, OutboundMessage
    from hahobot.session.manager import Session


class CommandRuntimeManager:
    """Own lightweight session routing and command-wrapper logic for AgentLoop."""

    def __init__(
        self,
        loop: AgentLoop,
        *,
        route_overrides: dict[str, str],
        unified_session: bool,
        unified_session_key: str,
    ) -> None:
        self.loop = loop
        self._route_overrides = route_overrides
        self._unified_session = unified_session
        self._unified_session_key = unified_session_key

    @staticmethod
    def _skill_subcommand(parts: list[str]) -> str | None:
        if len(parts) < 2:
            return None
        return parts[1].lower()

    @staticmethod
    def _skill_search_query(content: str) -> str | None:
        query_parts = content.strip().split(None, 2)
        if len(query_parts) < 3:
            return None
        query = query_parts[2].strip()
        return query or None

    @staticmethod
    def _skill_argument(parts: list[str]) -> str | None:
        if len(parts) < 3:
            return None
        value = parts[2].strip()
        return value or None

    @staticmethod
    def _persona_usage(language: str) -> str:
        return "\n".join([
            text(language, "cmd_persona_current"),
            text(language, "cmd_persona_list"),
            text(language, "cmd_persona_set"),
        ])

    @staticmethod
    def _stchar_usage(language: str) -> str:
        return text(language, "stchar_usage")

    @staticmethod
    def _preset_usage(language: str) -> str:
        return text(language, "preset_usage")

    @staticmethod
    def _language_usage(language: str) -> str:
        return "\n".join([
            text(language, "cmd_lang_current"),
            text(language, "cmd_lang_list"),
            text(language, "cmd_lang_set"),
        ])

    @staticmethod
    def _mcp_usage(language: str) -> str:
        return text(language, "mcp_usage")

    def command_context(
        self,
        msg: InboundMessage,
        *,
        session: Session | None = None,
        key: str | None = None,
    ) -> CommandContext:
        return CommandContext(
            msg=msg,
            session=session,
            key=key or msg.session_key,
            raw=msg.content.strip(),
            loop=self.loop,
        )

    def get_session_route(self, origin_key: str) -> str | None:
        """Return the active chat-level route override for one origin session key."""
        target = self._route_overrides.get(origin_key)
        if not target or target == origin_key:
            return None
        return target

    def set_session_route(self, origin_key: str, target_key: str) -> None:
        """Route one origin chat session to another logical session key."""
        if target_key == origin_key:
            self._route_overrides.pop(origin_key, None)
            return
        self._route_overrides[origin_key] = target_key

    def normalize_session_message(self, msg: InboundMessage) -> InboundMessage:
        """Apply unified-session routing unless the caller already pinned a session key."""
        metadata = dict(msg.metadata or {})
        origin_key = str(metadata.get("_origin_session_key") or msg.session_key)
        target_key = self.get_session_route(origin_key)
        if target_key:
            metadata["_origin_session_key"] = origin_key
            return dataclasses.replace(
                msg,
                metadata=metadata,
                session_key_override=target_key,
            )
        if self._unified_session and not msg.session_key_override:
            metadata["_origin_session_key"] = origin_key
            return dataclasses.replace(
                msg,
                metadata=metadata,
                session_key_override=self._unified_session_key,
            )
        return msg

    async def handle_skill_command(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        """Handle ClawHub skill management commands for the active workspace."""
        language = self.loop._get_session_language(session)
        parts = msg.content.strip().split()
        subcommand = self._skill_subcommand(parts)
        if not subcommand:
            return self._response(msg, text(language, "skill_usage"))

        if subcommand == "search":
            query = self._skill_search_query(msg.content)
            if not query:
                return self._response(msg, text(language, "skill_search_missing_query"))
            return await self.loop._skill_commands.search(msg, language, query)

        if subcommand == "install":
            slug = self._skill_argument(parts)
            if not slug:
                return self._response(msg, text(language, "skill_install_missing_slug"))
            return await self.loop._skill_commands.install(msg, language, slug)

        if subcommand == "uninstall":
            slug = self._skill_argument(parts)
            if not slug:
                return self._response(msg, text(language, "skill_uninstall_missing_slug"))
            return await self.loop._skill_commands.uninstall(msg, language, slug)

        if subcommand == "list":
            return await self.loop._skill_commands.list(msg, language)

        if subcommand == "update":
            return await self.loop._skill_commands.update(msg, language)

        return self._response(msg, text(language, "skill_usage"))

    async def handle_mcp_command(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        """Handle MCP inspection commands."""
        language = self.loop._get_session_language(session)
        parts = msg.content.strip().split()

        if len(parts) > 1 and parts[1].lower() != "list":
            return self._response(msg, self._mcp_usage(language))

        return await self.loop._mcp_commands.list(msg, language)

    async def handle_language_command(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        """Handle session-scoped language switching commands."""
        parts = msg.content.strip().split()
        current = self.loop._get_session_language(session)
        if len(parts) == 1 or parts[1].lower() == "current":
            return self.loop._language_commands.current(msg, session)

        subcommand = parts[1].lower()
        if subcommand == "list":
            return self.loop._language_commands.list(msg, session)

        if subcommand != "set" or len(parts) < 3:
            return self._response(msg, self._language_usage(current))

        return self.loop._language_commands.set(msg, session, parts[2])

    async def handle_persona_command(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        """Handle session-scoped persona management commands."""
        language = self.loop._get_session_language(session)
        parts = msg.content.strip().split()
        if len(parts) == 1 or parts[1].lower() == "current":
            return self.loop._persona_commands.current(msg, session)

        subcommand = parts[1].lower()
        if subcommand == "list":
            return self.loop._persona_commands.list(msg, session)

        if subcommand != "set" or len(parts) < 3:
            return self._response(msg, self._persona_usage(language))

        return await self.loop._persona_commands.set(msg, session, parts[2])

    async def handle_stchar_command(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        """Handle companion-friendly persona aliases."""
        language = self.loop._get_session_language(session)
        parts = msg.content.strip().split()
        if len(parts) == 1:
            return self.loop._stchar_commands.usage(msg, language)

        subcommand = parts[1].lower()
        if subcommand == "list":
            return self.loop._stchar_commands.list(msg, session)
        if subcommand == "show":
            if len(parts) < 3:
                return self.loop._stchar_commands.missing_name(msg, language)
            return self.loop._stchar_commands.show(msg, session, parts[2])
        if subcommand == "load":
            if len(parts) < 3:
                return self.loop._stchar_commands.missing_name(msg, language)
            return await self.loop._stchar_commands.load(msg, session, parts[2])

        return self._response(msg, self._stchar_usage(language))

    async def handle_preset_command(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        """Handle preset inspection commands."""
        language = self.loop._get_session_language(session)
        parts = msg.content.strip().split()
        if len(parts) == 1:
            return self.loop._preset_commands.show(msg, session)

        subcommand = parts[1].lower()
        if subcommand == "show":
            return self.loop._preset_commands.show(
                msg,
                session,
                parts[2] if len(parts) > 2 else None,
            )

        return self._response(msg, self._preset_usage(language))

    async def handle_scene_command(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        """Handle companion scene shortcut commands."""
        language = self.loop._get_session_language(session)
        parts = msg.content.strip().split(maxsplit=2)
        if len(parts) == 1:
            return self.loop._scene_commands.usage(msg, language)

        subcommand = parts[1].lower()
        if subcommand == "list":
            return self.loop._scene_commands.list(msg, session)
        if subcommand == "generate":
            if len(parts) < 3 or not parts[2].strip():
                return self.loop._scene_commands.missing_brief(msg, language)
            return await self.loop._scene_commands.generate(
                msg,
                session,
                subcommand=subcommand,
                brief=parts[2],
            )

        return await self.loop._scene_commands.generate(msg, session, subcommand=subcommand)

    @staticmethod
    def _response(msg: InboundMessage, content: str) -> OutboundMessage:
        from hahobot.bus.events import OutboundMessage

        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)
