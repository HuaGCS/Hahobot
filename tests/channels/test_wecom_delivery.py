"""Delivery error propagation for the WeCom channel."""

from __future__ import annotations

import pytest

from hahobot.bus.events import OutboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.channels.wecom import WecomChannel
from hahobot.config.schema import WecomConfig


@pytest.mark.asyncio
async def test_send_raises_when_client_is_not_initialized() -> None:
    channel = WecomChannel(
        WecomConfig(enabled=True, bot_id="bot", secret="secret", allow_from=["*"]),
        MessageBus(),
    )

    with pytest.raises(RuntimeError, match="client is not initialized"):
        await channel.send(OutboundMessage(channel="wecom", chat_id="chat1", content="hello"))


@pytest.mark.asyncio
async def test_send_raises_when_chat_frame_is_unavailable() -> None:
    channel = WecomChannel(
        WecomConfig(enabled=True, bot_id="bot", secret="secret", allow_from=["*"]),
        MessageBus(),
    )
    channel._client = object()

    with pytest.raises(RuntimeError, match="chat frame is unavailable"):
        await channel.send(OutboundMessage(channel="wecom", chat_id="chat1", content="hello"))
