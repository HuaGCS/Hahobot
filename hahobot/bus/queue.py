"""Async message queue for decoupled channel-agent communication."""

import asyncio
import logging

from hahobot.bus.events import InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)

# Maximum number of messages that can queue before back-pressure kicks in.
# Prevents unbounded memory growth when the agent is slower than inbound rate.
_DEFAULT_INBOUND_MAXSIZE = 1000
_DEFAULT_OUTBOUND_MAXSIZE = 1000


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.

    Both queues are bounded (default 1 000 slots each). When a queue is full,
    publishers wait until the consumer catches up, which applies natural
    back-pressure and prevents unbounded memory growth.
    """

    def __init__(
        self,
        inbound_maxsize: int = _DEFAULT_INBOUND_MAXSIZE,
        outbound_maxsize: int = _DEFAULT_OUTBOUND_MAXSIZE,
    ):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=inbound_maxsize)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(maxsize=outbound_maxsize)

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        if self.inbound.full():
            logger.warning(
                "Inbound queue full (%d slots), waiting for capacity for %s/%s",
                self.inbound.maxsize,
                msg.channel,
                msg.chat_id,
            )
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        if self.outbound.full():
            logger.warning(
                "Outbound queue full (%d slots), waiting for capacity for %s/%s",
                self.outbound.maxsize,
                msg.channel,
                msg.chat_id,
            )
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
