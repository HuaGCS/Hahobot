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
from collections import OrderedDict
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
        self._active_turns: dict[str, dict[str, Any]] = {}
        self._turn_tasks: dict[str, asyncio.Task[None]] = {}
        self._completed_turns: OrderedDict[str, str] = OrderedDict()
        self._closed = False

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

    def begin_turn(
        self,
        session_key: str,
        *,
        request_id: str,
        text: str,
        display_user: bool,
    ) -> bool:
        """Claim one in-flight user turn for a session without awaiting or racing."""
        if self._closed or session_key in self._active_turns:
            return False
        self._active_turns[session_key] = {
            "request_id": request_id,
            "text": text,
            "display_user": display_user,
        }
        return True

    def end_turn(self, session_key: str, *, completed: bool = False) -> None:
        """Release a turn claim and retain a bounded completion receipt."""
        turn = self._active_turns.pop(session_key, None)
        if not completed or turn is None:
            return
        request_id = str(turn.get("request_id") or "")
        if not request_id:
            return
        self._completed_turns[session_key] = request_id
        self._completed_turns.move_to_end(session_key)
        while len(self._completed_turns) > 128:
            self._completed_turns.popitem(last=False)

    def track_turn_task(self, session_key: str, task: asyncio.Task[None]) -> None:
        """Keep a detached turn alive after its initiating WebSocket disconnects."""
        if self._closed:
            task.cancel()
            return
        self._turn_tasks[session_key] = task

        def _discard(done: asyncio.Task[None]) -> None:
            if self._turn_tasks.get(session_key) is done:
                self._turn_tasks.pop(session_key, None)
            try:
                error = done.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                logger.error("detached webui turn failed for {}: {}", session_key, error)

        task.add_done_callback(_discard)

    def turn_active(self, session_key: str) -> bool:
        """Return whether this process is already handling a turn for the session."""
        return session_key in self._active_turns

    def active_turn(self, session_key: str) -> dict[str, Any] | None:
        """Return a detached active-turn receipt for reconnect recovery."""
        turn = self._active_turns.get(session_key)
        return dict(turn) if turn is not None else None

    def completed_request_id(self, session_key: str) -> str | None:
        """Return the most recent completed request id for reconnect reconciliation."""
        return self._completed_turns.get(session_key)

    async def close(self) -> None:
        """Cancel and await detached turns during gateway shutdown."""
        self._closed = True
        tasks = list(self._turn_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._turn_tasks.clear()
        self._active_turns.clear()

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
