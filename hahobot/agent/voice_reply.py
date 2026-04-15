"""Voice reply resolution and synthesis helpers."""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from hahobot.agent.personas import build_persona_voice_instructions, load_persona_voice_settings
from hahobot.bus.events import OutboundMessage
from hahobot.providers.speech import (
    EdgeSpeechProvider,
    GPTSoVITSSpeechProvider,
    OpenAISpeechProvider,
)
from hahobot.utils.helpers import ensure_dir, safe_filename

if TYPE_CHECKING:
    from hahobot.config.schema import ChannelsConfig
    from hahobot.providers.base import LLMProvider


@dataclasses.dataclass(frozen=True, slots=True)
class VoiceReplyProfile:
    """Resolved provider-specific voice reply settings for one turn."""

    provider: str
    voice: str
    instructions: str
    speed: float | None
    api_base: str
    rate: str
    volume: str
    sovits_api_url: str
    sovits_refer_wav_path: str
    sovits_prompt_text: str
    sovits_prompt_language: str
    sovits_text_language: str
    sovits_cut_punc: str
    sovits_top_k: int
    sovits_top_p: float
    sovits_temperature: float


class VoiceReplyHandler:
    """Resolve and synthesize optional outbound TTS attachments."""

    def __init__(
        self,
        *,
        workspace: Path,
        channels_config: ChannelsConfig | None,
        provider: LLMProvider | Any,
    ) -> None:
        self.workspace = workspace
        self.channels_config = channels_config
        self.provider = provider

    def update_runtime(
        self,
        *,
        workspace: Path,
        channels_config: ChannelsConfig | None,
    ) -> None:
        """Refresh runtime-bound workspace and channel config references."""
        self.workspace = workspace
        self.channels_config = channels_config

    @staticmethod
    def extension(response_format: str) -> str:
        """Map TTS response formats to delivery file extensions."""
        return {
            "opus": ".ogg",
            "mp3": ".mp3",
            "aac": ".aac",
            "flac": ".flac",
            "wav": ".wav",
            "pcm": ".pcm",
            "silk": ".silk",
        }.get(response_format, f".{response_format}")

    @staticmethod
    def _channel_base_name(channel: str) -> str:
        """Normalize multi-instance channel routes such as telegram/main."""
        return channel.split("/", 1)[0].lower()

    def enabled_for_channel(self, channel: str) -> bool:
        """Return True when voice replies are enabled for the given channel."""
        cfg = getattr(self.channels_config, "voice_reply", None)
        if not cfg or not getattr(cfg, "enabled", False):
            return False
        route_name = channel.lower()
        base_name = self._channel_base_name(channel)
        enabled_channels = {
            name.lower() for name in getattr(cfg, "channels", []) if isinstance(name, str)
        }
        if route_name not in enabled_channels and base_name not in enabled_channels:
            return False
        if base_name == "qq":
            return getattr(cfg, "response_format", "opus") == "silk"
        return base_name in {"telegram", "qq"}

    def profile(self, persona: str | None) -> VoiceReplyProfile:
        """Resolve provider-specific voice settings for the active persona."""
        cfg = getattr(self.channels_config, "voice_reply", None)
        persona_voice = load_persona_voice_settings(self.workspace, persona)
        provider_name = persona_voice.provider or getattr(cfg, "provider", "openai")

        extra_instructions = [
            value.strip()
            for value in (
                getattr(cfg, "instructions", "") if cfg is not None else "",
                persona_voice.instructions or "",
            )
            if isinstance(value, str) and value.strip()
        ]
        instructions = build_persona_voice_instructions(
            self.workspace,
            persona,
            extra_instructions=" ".join(extra_instructions) if extra_instructions else None,
        )
        speed = (
            persona_voice.speed
            if persona_voice.speed is not None
            else getattr(cfg, "speed", None) if cfg is not None else None
        )
        if provider_name == "edge":
            voice = persona_voice.voice or getattr(cfg, "edge_voice", "zh-CN-XiaoxiaoNeural")
        else:
            voice = persona_voice.voice or getattr(cfg, "voice", "alloy")

        return VoiceReplyProfile(
            provider=provider_name,
            voice=voice,
            instructions=instructions,
            speed=speed,
            api_base=persona_voice.api_base or getattr(cfg, "api_base", ""),
            rate=persona_voice.rate or getattr(cfg, "edge_rate", "+0%"),
            volume=persona_voice.volume or getattr(cfg, "edge_volume", "+0%"),
            sovits_api_url=persona_voice.api_base or getattr(cfg, "sovits_api_url", ""),
            sovits_refer_wav_path=persona_voice.refer_wav_path
            or getattr(cfg, "sovits_refer_wav_path", ""),
            sovits_prompt_text=persona_voice.prompt_text or getattr(cfg, "sovits_prompt_text", ""),
            sovits_prompt_language=persona_voice.prompt_language
            or getattr(cfg, "sovits_prompt_language", "zh"),
            sovits_text_language=persona_voice.text_language
            or getattr(cfg, "sovits_text_language", "zh"),
            sovits_cut_punc=persona_voice.cut_punc or getattr(cfg, "sovits_cut_punc", "，。"),
            sovits_top_k=persona_voice.top_k
            if persona_voice.top_k is not None
            else getattr(cfg, "sovits_top_k", 5),
            sovits_top_p=persona_voice.top_p
            if persona_voice.top_p is not None
            else getattr(cfg, "sovits_top_p", 1.0),
            sovits_temperature=persona_voice.temperature
            if persona_voice.temperature is not None
            else getattr(cfg, "sovits_temperature", 1.0),
        )

    @staticmethod
    def response_format(provider_name: str, configured_format: str) -> str:
        """Resolve the final output format for the selected voice provider."""
        if provider_name == "edge":
            return "mp3"
        if provider_name == "sovits" and configured_format == "opus":
            return "wav"
        return configured_format

    async def maybe_attach(
        self,
        outbound: OutboundMessage | None,
        *,
        persona: str | None = None,
    ) -> OutboundMessage | None:
        """Optionally synthesize the final text reply into a voice attachment."""
        if (
            outbound is None
            or not outbound.content
            or not self.enabled_for_channel(outbound.channel)
        ):
            return outbound

        cfg = getattr(self.channels_config, "voice_reply", None)
        if cfg is None:
            return outbound

        profile = self.profile(persona)
        provider_name = profile.provider
        response_format = self.response_format(
            provider_name,
            getattr(cfg, "response_format", "opus"),
        )
        model = getattr(cfg, "model", "gpt-4o-mini-tts")
        media_dir = ensure_dir(self.workspace / "out" / "voice")
        filename = safe_filename(
            f"{outbound.channel}_{outbound.chat_id}_{int(time.time() * 1000)}"
        ) + self.extension(response_format)
        output_path = media_dir / filename

        try:
            if provider_name == "edge":
                provider = EdgeSpeechProvider(
                    voice=profile.voice,
                    rate=profile.rate,
                    volume=profile.volume,
                )
                await provider.synthesize_to_file(outbound.content, output_path=output_path)
            elif provider_name == "sovits":
                provider = GPTSoVITSSpeechProvider(
                    api_url=(profile.sovits_api_url or "http://127.0.0.1:9880").strip(),
                    refer_wav_path=profile.sovits_refer_wav_path,
                    prompt_text=profile.sovits_prompt_text,
                    prompt_language=profile.sovits_prompt_language,
                    text_language=profile.sovits_text_language,
                    cut_punc=profile.sovits_cut_punc,
                    top_k=profile.sovits_top_k,
                    top_p=profile.sovits_top_p,
                    temperature=profile.sovits_temperature,
                    speed=profile.speed or 1.0,
                )
                await provider.synthesize_to_file(outbound.content, output_path=output_path)
            else:
                api_key = (
                    getattr(cfg, "api_key", "") or getattr(self.provider, "api_key", "") or ""
                ).strip()
                if not api_key:
                    logger.warning(
                        "Voice reply enabled for {}, but no TTS api_key is configured",
                        outbound.channel,
                    )
                    return outbound
                api_base = (
                    profile.api_base
                    or getattr(self.provider, "api_base", "")
                    or "https://api.openai.com/v1"
                ).strip()
                provider = OpenAISpeechProvider(api_key=api_key, api_base=api_base)
                await provider.synthesize_to_file(
                    outbound.content,
                    model=model,
                    voice=profile.voice,
                    instructions=profile.instructions,
                    speed=profile.speed,
                    response_format=response_format,
                    output_path=output_path,
                )
        except Exception:
            logger.exception(
                "Failed to synthesize voice reply for {}:{}",
                outbound.channel,
                outbound.chat_id,
            )
            return outbound

        return OutboundMessage(
            channel=outbound.channel,
            chat_id=outbound.chat_id,
            content=outbound.content,
            reply_to=outbound.reply_to,
            media=[*(outbound.media or []), str(output_path)],
            metadata=dict(outbound.metadata or {}),
        )
