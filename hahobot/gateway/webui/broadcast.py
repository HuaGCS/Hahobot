"""In-process fan-out for pushing frames to connected WebUI chat clients.

The WebUI WebSocket (``/app/ws``) is request-scoped, so proactive / scheduled
output (cron, heartbeat, the cross-session ``message`` tool) cannot be delivered
by the normal "reply to the current turn" path. Instead, each live connection
registers here under its session key, and a proactive ``OutboundMessage`` routed
through the ``webui`` pseudo-channel is broadcast to every matching connection.

When no client is connected the broadcast is a no-op — the message still reaches
the user because it is persisted into the ``webui:<id>`` session by
``AgentLoop._record_proactive_delivery`` and rendered on the next page load.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class WebUIConnection:
    """One live ``/app/ws`` client: a single-writer send queue.

    All outbound frames for a connection — both the frames produced by the user's
    own turn and server-pushed frames — go through ``queue``, drained by exactly
    one writer task, so two coroutines never write the same WebSocket concurrently.
    """

    def __init__(self, session_key: str, maxsize: int = 256) -> None:
        self.session_key = session_key
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=maxsize)

    def enqueue(self, frame: dict[str, Any]) -> None:
        """Queue a frame for the writer task; drop (never block) if the client lags."""
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning("webui connection queue full; dropping frame for {}", self.session_key)

    def close(self) -> None:
        """Signal the writer task to stop by enqueuing a sentinel."""
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass


class WebUIBroadcaster:
    """Registry of live WebUI connections keyed by session key."""

    def __init__(self) -> None:
        self._conns: dict[str, set[WebUIConnection]] = {}

    def register(self, conn: WebUIConnection) -> None:
        self._conns.setdefault(conn.session_key, set()).add(conn)

    def unregister(self, conn: WebUIConnection) -> None:
        conns = self._conns.get(conn.session_key)
        if not conns:
            return
        conns.discard(conn)
        if not conns:
            self._conns.pop(conn.session_key, None)

    def connection_count(self, session_key: str) -> int:
        return len(self._conns.get(session_key, ()))

    async def broadcast(self, session_key: str, frame: dict[str, Any]) -> int:
        """Deliver ``frame`` to every connection registered under ``session_key``.

        Iterates a snapshot so a concurrent (un)register cannot mutate the set
        mid-iteration; returns the number of connections the frame was queued for.
        """
        conns = list(self._conns.get(session_key, ()))
        for conn in conns:
            try:
                conn.enqueue(frame)
            except Exception as exc:  # noqa: BLE001 - best-effort fan-out
                logger.debug("webui broadcast to {} failed: {}", session_key, exc)
        return len(conns)
