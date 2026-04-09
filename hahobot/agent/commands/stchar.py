"""SillyTavern-style persona command aliases for AgentLoop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hahobot.agent.i18n import text
from hahobot.agent.personas import summarize_persona_assets
from hahobot.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.session.manager import Session


class STCharCommandHandler:
    """Companion-friendly aliases over the existing persona workflow."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    @staticmethod
    def _response(msg: InboundMessage, content: str) -> OutboundMessage:
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)

    def usage(self, msg: InboundMessage, language: str) -> OutboundMessage:
        return self._response(msg, text(language, "stchar_usage"))

    def missing_name(self, msg: InboundMessage, language: str) -> OutboundMessage:
        return self._response(msg, text(language, "stchar_missing_name"))

    def list(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        return self.loop._persona_commands.list(msg, session)

    def show(self, msg: InboundMessage, session: Session, target_raw: str) -> OutboundMessage:
        language = self.loop._get_session_language(session)
        target = self.loop.context.find_persona(target_raw)
        if target is None:
            personas = ", ".join(self.loop.context.list_personas())
            return self._response(
                msg,
                text(
                    language,
                    "unknown_persona",
                    name=target_raw,
                    personas=personas,
                    path=self.loop.workspace / "personas" / target_raw,
                ),
            )

        summary = summarize_persona_assets(self.loop.workspace, target)
        if summary is None:
            return self._response(msg, text(language, "generic_error"))

        present = text(language, "state_present")
        missing = text(language, "state_missing")
        none = text(language, "state_none")
        tags = ", ".join(summary.response_filter_tags) or none
        return self._response(
            msg,
            text(
                language,
                "stchar_summary",
                persona=summary.resolved_name,
                path=summary.persona_dir,
                has_soul=present if summary.has_soul else missing,
                has_user=present if summary.has_user else missing,
                has_style=present if summary.has_style else missing,
                has_lore=present if summary.has_lore else missing,
                has_voice=present if summary.has_voice else missing,
                has_manifest=present if summary.has_manifest else missing,
                has_preset=present if summary.has_preset else missing,
                has_world_info=present if summary.has_world_info else missing,
                reference_count=summary.reference_image_count,
                tags=tags,
            ),
        )

    async def load(self, msg: InboundMessage, session: Session, target_raw: str) -> OutboundMessage:
        return await self.loop._persona_commands.set(msg, session, target_raw)
