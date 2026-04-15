from __future__ import annotations

import asyncio

import pytest

from hahobot.bus.events import InboundMessage, OutboundMessage
from hahobot.bus.queue import MessageBus


@pytest.mark.asyncio
async def test_publish_inbound_waits_for_capacity() -> None:
    bus = MessageBus(inbound_maxsize=1)
    first = InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="one")
    second = InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="two")

    await bus.publish_inbound(first)
    blocked = asyncio.create_task(bus.publish_inbound(second))
    await asyncio.sleep(0)

    assert not blocked.done()

    consumed = await bus.consume_inbound()
    assert consumed.content == "one"

    await asyncio.wait_for(blocked, timeout=1)
    assert bus.inbound_size == 1
    assert (await bus.consume_inbound()).content == "two"


@pytest.mark.asyncio
async def test_publish_outbound_waits_for_capacity() -> None:
    bus = MessageBus(outbound_maxsize=1)
    first = OutboundMessage(channel="cli", chat_id="c1", content="one")
    second = OutboundMessage(channel="cli", chat_id="c1", content="two")

    await bus.publish_outbound(first)
    blocked = asyncio.create_task(bus.publish_outbound(second))
    await asyncio.sleep(0)

    assert not blocked.done()

    consumed = await bus.consume_outbound()
    assert consumed.content == "one"

    await asyncio.wait_for(blocked, timeout=1)
    assert bus.outbound_size == 1
    assert (await bus.consume_outbound()).content == "two"
