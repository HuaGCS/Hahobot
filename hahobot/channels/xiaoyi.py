"""Huawei Xiaoyi (华为小艺) voice-assistant channel — stub scaffold.

Hahobot has no actual Xiaoyi integration yet. The class exists so the channel
registry, admin config form, and discovery infrastructure can already reference
the name (`xiaoyi`), and so a future contributor only needs to fill in the
HiAI / 小艺技能开放平台 / 华为云 MaaS calls.

Disabled-by-default: the no-arg ``XiaoyiConfig`` has ``enabled=False`` and the
``start()`` method short-circuits in that state, so a fresh ``Config()`` will
not crash the gateway. When ``enabled=True`` and the stub is reached, ``start``
emits a clear warning and exits — the channel does not pretend to be alive.
"""

from __future__ import annotations

from loguru import logger

from hahobot.bus.events import OutboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.channels.base import BaseChannel, NonRetriableSendError
from hahobot.config.schema import XiaoyiConfig


class XiaoyiChannel(BaseChannel):
    """Stub channel for Huawei Xiaoyi voice integration."""

    name = "xiaoyi"
    display_name = "Xiaoyi (华为小艺)"

    @classmethod
    def default_config(cls) -> dict[str, object]:
        return XiaoyiConfig().model_dump(by_alias=True)

    def __init__(self, config: XiaoyiConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: XiaoyiConfig = config

    async def start(self) -> None:
        """Mark the channel as 'running' but do nothing real.

        When ``enabled=False`` (default) this is a silent no-op so the
        ChannelManager bring-up does not blow up. When ``enabled=True`` we log
        a loud warning so operators don't believe the channel is alive.
        """
        if not self.config.enabled:
            return
        logger.warning(
            "Xiaoyi channel is currently a stub — no real Huawei integration yet. "
            "See hahobot/channels/xiaoyi.py for the contract to implement against."
        )
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """Refuse outbound delivery until the real integration is in place."""
        raise NonRetriableSendError(
            "Xiaoyi channel is a stub; outbound delivery is not implemented. "
            "Disable channels.xiaoyi.enabled or implement the Huawei integration."
        )
