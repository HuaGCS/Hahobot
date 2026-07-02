"""A pseudo-channel that delivers outbound messages to connected WebUI clients.

Registered into ``ChannelManager.channels`` under the name ``webui`` so that a
proactive ``OutboundMessage(channel="webui", chat_id="<id>")`` (from cron,
heartbeat, or the cross-session ``message`` tool) is routed by the existing
``_dispatch_outbound`` loop to ``send()`` here, which fans the message out to
live ``/app/ws`` clients via the shared :class:`WebUIBroadcaster`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hahobot.bus.events import OutboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.channels.base import BaseChannel
from hahobot.gateway.webui.app import _media_url_for
from hahobot.gateway.webui.broadcast import WebUIBroadcaster

_WEBUI_SESSION_PREFIX = "webui:"


class WebUIChannel(BaseChannel):
    """Deliver proactive output to connected WebUI clients (best-effort)."""

    name = "webui"
    display_name = "WebUI"

    def __init__(self, broadcaster: WebUIBroadcaster, bus: MessageBus, workspace: Path) -> None:
        super().__init__(config=None, bus=bus)
        self._broadcaster = broadcaster
        self._workspace = Path(workspace)
        self._stop = asyncio.Event()

    async def start(self) -> None:
        # Idle for the process lifetime so ChannelManager's outbound dispatcher
        # stays running even when the WebUI is the only enabled surface.
        self._stop.clear()
        await self._stop.wait()

    async def stop(self) -> None:
        self._stop.set()

    async def send(self, msg: OutboundMessage) -> None:
        session_key = f"{_WEBUI_SESSION_PREFIX}{msg.chat_id}"
        media: list[str] = []
        for item in msg.media or []:
            url = _media_url_for(str(item), self._workspace / "out")
            if url:
                media.append(url)
        frame = {"event": "push", "text": msg.content or "", "media": media}
        await self._broadcaster.broadcast(session_key, frame)
