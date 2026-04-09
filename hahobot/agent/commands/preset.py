"""Preset inspection commands for AgentLoop personas."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hahobot.agent.i18n import text
from hahobot.agent.personas import (
    PERSONA_METADATA_DIRNAME,
    PERSONA_ST_PRESET_FILENAME,
    PERSONA_STYLE_FILENAME,
    summarize_persona_assets,
)
from hahobot.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.session.manager import Session


class PresetCommandHandler:
    """Inspect imported SillyTavern preset artifacts for the active persona."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    @staticmethod
    def _response(msg: InboundMessage, content: str) -> OutboundMessage:
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)

    def usage(self, msg: InboundMessage, language: str) -> OutboundMessage:
        return self._response(msg, text(language, "preset_usage"))

    def show(
        self,
        msg: InboundMessage,
        session: Session,
        target_raw: str | None = None,
    ) -> OutboundMessage:
        language = self.loop._get_session_language(session)
        target_name = target_raw or self.loop._get_session_persona(session)
        target = self.loop.context.find_persona(target_name)
        if target is None:
            personas = ", ".join(self.loop.context.list_personas())
            return self._response(
                msg,
                text(
                    language,
                    "unknown_persona",
                    name=target_name,
                    personas=personas,
                    path=self.loop.workspace / "personas" / str(target_name),
                ),
            )

        summary = summarize_persona_assets(self.loop.workspace, target)
        if summary is None:
            return self._response(msg, text(language, "generic_error"))

        style_path = summary.persona_dir / PERSONA_STYLE_FILENAME
        preset_path = summary.persona_dir / PERSONA_METADATA_DIRNAME / PERSONA_ST_PRESET_FILENAME
        if not summary.has_style and not summary.has_preset:
            return self._response(
                msg,
                text(
                    language,
                    "preset_missing",
                    persona=summary.resolved_name,
                    style_path=style_path,
                    preset_path=preset_path,
                ),
            )

        present = text(language, "state_present")
        missing = text(language, "state_missing")
        return self._response(
            msg,
            text(
                language,
                "preset_summary",
                persona=summary.resolved_name,
                path=summary.persona_dir,
                has_style=present if summary.has_style else missing,
                has_preset=present if summary.has_preset else missing,
            ),
        )
