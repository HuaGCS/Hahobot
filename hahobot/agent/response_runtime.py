"""Response visibility, prompt-section, and voice-reply helpers for AgentLoop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hahobot.agent.personas import (
    load_persona_response_filter_tags,
    strip_tagged_response_content,
)
from hahobot.agent.voice_reply import VoiceReplyHandler

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.agent.voice_reply import VoiceReplyProfile
    from hahobot.bus.events import OutboundMessage


class ResponseRuntimeManager:
    """Own user-visible response filtering and prompt-section helpers for AgentLoop."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    @staticmethod
    def append_system_section(
        messages: list[dict[str, Any]],
        title: str,
        content: str,
    ) -> None:
        """Append an extra section to the system prompt if present."""
        if not content or not messages:
            return
        system = messages[0]
        if system.get("role") != "system" or not isinstance(system.get("content"), str):
            return
        system["content"] += f"\n\n---\n\n# {title}\n\n{content}"

    @staticmethod
    def indented_system_data_block(content: str) -> str:
        """Render untrusted tool output as an indented data block, not free-form prompt text."""
        return "\n".join(f"    {line}" if line else "    " for line in content.splitlines())

    def append_untrusted_system_section(
        self,
        messages: list[dict[str, Any]],
        title: str,
        content: str,
    ) -> None:
        """Append untrusted MCP output as data so it cannot masquerade as system instructions."""
        if not content or not messages:
            return
        system = messages[0]
        if system.get("role") != "system" or not isinstance(system.get("content"), str):
            return
        payload = self.indented_system_data_block(content)
        system["content"] += (
            f"\n\n---\n\n# {title}\n\n{self.loop._UNTRUSTED_MCP_BANNER}\n\n{payload}"
        )

    @staticmethod
    def strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        from hahobot.utils.helpers import strip_think

        return strip_think(text) or None

    def filter_persona_response(self, text: str | None, persona: str | None) -> str | None:
        """Apply persona-level response filtering for user-visible output only."""
        if text is None:
            return None
        tags = load_persona_response_filter_tags(self.loop.workspace, persona)
        if not tags:
            return text
        return strip_tagged_response_content(text, tags)

    def visible_response_text(self, text: str | None, persona: str | None) -> str:
        """Return the user-visible version of a model response."""
        clean = self.strip_think(text) or ""
        return self.filter_persona_response(clean, persona) or ""

    @staticmethod
    def tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hints with smart abbreviation."""
        from hahobot.utils.tool_hints import format_tool_hints

        return format_tool_hints(tool_calls)

    @staticmethod
    def voice_reply_extension(response_format: str) -> str:
        """Map TTS response formats to delivery file extensions."""
        return VoiceReplyHandler.extension(response_format)

    @staticmethod
    def channel_base_name(channel: str) -> str:
        """Normalize multi-instance channel routes such as telegram/main."""
        return channel.split("/", 1)[0].lower()

    def voice_reply_enabled_for_channel(self, channel: str) -> bool:
        """Return True when voice replies are enabled for the given channel."""
        return self.loop.voice_replies.enabled_for_channel(channel)

    def voice_reply_profile(self, persona: str | None) -> VoiceReplyProfile:
        """Resolve provider-specific voice settings for the active persona."""
        return self.loop.voice_replies.profile(persona)

    @staticmethod
    def voice_reply_response_format(provider_name: str, configured_format: str) -> str:
        """Resolve the final output format for the selected voice provider."""
        return VoiceReplyHandler.response_format(provider_name, configured_format)

    async def maybe_attach_voice_reply(
        self,
        outbound: OutboundMessage | None,
        *,
        persona: str | None = None,
    ) -> OutboundMessage | None:
        """Optionally synthesize the final text reply into a voice attachment."""
        return await self.loop.voice_replies.maybe_attach(outbound, persona=persona)
