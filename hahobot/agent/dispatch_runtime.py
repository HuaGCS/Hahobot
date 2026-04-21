"""Inbound queue consumption and per-session dispatch helpers."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import nullcontext
from typing import TYPE_CHECKING

from loguru import logger

from hahobot.agent.hook_bridge import ExternalHookBridgeBlocked
from hahobot.agent.i18n import text
from hahobot.bus.events import OutboundMessage

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.events import InboundMessage


class DispatchRuntimeManager:
    """Own queue consumption, per-session locking, and streamed dispatch plumbing."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self.loop._running = True
        await self.loop._connect_mcp()
        logger.info("Agent loop started")

        while self.loop._running:
            try:
                msg = await asyncio.wait_for(self.loop.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                self.loop.auto_compact.check_expired(
                    self.loop._schedule_background,
                    active_session_keys=self.active_session_keys(),
                )
                continue
            except asyncio.CancelledError:
                if not self.loop._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as exc:
                logger.warning("Error consuming inbound message: {}, continuing...", exc)
                continue

            msg = self.loop._normalize_session_message(msg)
            ctx = self.loop._command_context(
                msg,
                session=self.loop.sessions.get_or_create(msg.session_key),
            )
            if self.loop._command_router.is_priority(ctx.raw):
                result = await self.loop._command_router.dispatch_priority(ctx)
                if result is not None:
                    await self.loop.bus.publish_outbound(result)
                continue

            task = asyncio.create_task(self.loop._dispatch(msg))
            self.loop._active_tasks.setdefault(msg.session_key, []).append(task)
            task.add_done_callback(
                lambda done, key=msg.session_key: self.discard_active_task(key, done)
            )

    async def handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        msg = self.loop._normalize_session_message(msg)
        tasks = self.loop._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for task in tasks if not task.done() and task.cancel())
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.loop.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        session = self.loop.sessions.get_or_create(msg.session_key)
        language = self.loop._get_session_language(session)
        content = (
            text(language, "stopped_tasks", count=total)
            if total
            else text(language, "no_active_task")
        )
        await self.loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
        ))

    async def handle_restart(self, msg: InboundMessage) -> None:
        """Restart the process in-place via os.execv."""
        session = self.loop.sessions.get_or_create(msg.session_key)
        language = self.loop._get_session_language(session)
        await self.loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=text(language, "restarting"),
        ))

        async def _do_restart() -> None:
            await asyncio.sleep(1)
            os.execv(sys.executable, [sys.executable, "-m", "hahobot"] + sys.argv[1:])

        asyncio.create_task(_do_restart())

    def discard_active_task(self, session_key: str, task: asyncio.Task) -> None:
        """Remove a finished task from active-task tracking."""
        tasks = self.loop._active_tasks.get(session_key)
        if not tasks:
            return
        if task in tasks:
            tasks.remove(task)
        if not tasks:
            self.loop._active_tasks.pop(session_key, None)

    def active_session_keys(self) -> set[str]:
        """Return session keys that still have an in-flight agent task."""
        active: set[str] = set()
        for key, tasks in list(self.loop._active_tasks.items()):
            live = [task for task in tasks if not task.done()]
            if live:
                if len(live) != len(tasks):
                    self.loop._active_tasks[key] = live
                active.add(key)
                continue
            self.loop._active_tasks.pop(key, None)
        return active

    async def dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        msg = self.loop._normalize_session_message(msg)
        lock = self.loop._session_locks.get(msg.session_key)
        if lock is None:
            lock = asyncio.Lock()
            self.loop._session_locks[msg.session_key] = lock
        gate = self.loop._concurrency_gate or nullcontext()
        async with lock, gate:
            try:
                on_stream, on_stream_end = self._stream_callbacks(msg)
                response = await self.loop._process_message(
                    msg,
                    on_stream=on_stream,
                    on_stream_end=on_stream_end,
                )
                if response is not None:
                    await self.loop.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.loop.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="",
                        metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                session = self.loop.sessions.get_or_create(msg.session_key)
                restored = self.loop._restore_runtime_checkpoint(session)
                restored = self.loop._restore_pending_user_turn(session) or restored
                if restored:
                    self.loop.sessions.save(session)
                raise
            except ExternalHookBridgeBlocked as exc:
                logger.info("External hook blocked session {}: {}", msg.session_key, exc)
                await self.loop.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=str(exc) or text(
                        self.loop._get_session_language(
                            self.loop.sessions.get_or_create(msg.session_key)
                        ),
                        "generic_error",
                    ),
                ))
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.loop.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=text(
                        self.loop._get_session_language(
                            self.loop.sessions.get_or_create(msg.session_key)
                        ),
                        "generic_error",
                    ),
                ))

    def track_background_task(self, task: asyncio.Task) -> asyncio.Task:
        """Track a background task until completion."""
        self.loop._background_tasks.add(task)
        task.add_done_callback(self.loop._background_tasks.discard)
        return task

    def schedule_background(self, coro) -> asyncio.Task:
        """Schedule a coroutine as a tracked background task."""
        task = asyncio.create_task(coro)
        return self.track_background_task(task)

    def _stream_callbacks(self, msg: InboundMessage):
        """Build optional streamed delta/end callbacks for one inbound message."""
        if not msg.metadata.get("_wants_stream"):
            return None, None

        stream_base_id = f"{msg.session_key}:{time.time_ns()}"
        stream_segment = 0

        def _current_stream_id() -> str:
            return f"{stream_base_id}:{stream_segment}"

        async def on_stream(delta: str) -> None:
            meta = dict(msg.metadata or {})
            meta["_stream_delta"] = True
            meta["_stream_id"] = _current_stream_id()
            await self.loop.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=delta,
                metadata=meta,
            ))

        async def on_stream_end(*, resuming: bool = False) -> None:
            nonlocal stream_segment
            meta = dict(msg.metadata or {})
            meta["_stream_end"] = True
            meta["_resuming"] = resuming
            meta["_stream_id"] = _current_stream_id()
            await self.loop.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="",
                metadata=meta,
            ))
            stream_segment += 1

        return on_stream, on_stream_end
