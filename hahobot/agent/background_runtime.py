"""Background-task and shutdown helpers for AgentLoop."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.session.manager import Session


class BackgroundRuntimeManager:
    """Own tracked background work, token consolidation, and shutdown helpers."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self.loop._background_tasks:
            await asyncio.gather(*list(self.loop._background_tasks), return_exceptions=True)
            self.loop._background_tasks.clear()
        self.loop._token_consolidation_tasks.clear()
        await self.loop._reset_mcp_connections()

    def track_background_task(self, task: asyncio.Task) -> asyncio.Task:
        """Track a background task until completion."""
        return self.loop._dispatch_runtime.track_background_task(task)

    def schedule_background(self, coro) -> asyncio.Task:
        """Schedule a coroutine as a tracked background task."""
        return self.loop._dispatch_runtime.schedule_background(coro)

    def ensure_background_token_consolidation(self, session: Session) -> asyncio.Task[None]:
        """Ensure at most one token-consolidation task runs per session."""
        existing = self.loop._token_consolidation_tasks.get(session.key)
        if existing and not existing.done():
            return existing

        task = asyncio.create_task(self.loop.memory_consolidator.maybe_consolidate_by_tokens(session))
        self.loop._token_consolidation_tasks[session.key] = task
        self.track_background_task(task)

        def _cleanup(done: asyncio.Task[None]) -> None:
            if self.loop._token_consolidation_tasks.get(session.key) is done:
                self.loop._token_consolidation_tasks.pop(session.key, None)

        task.add_done_callback(_cleanup)
        return task

    async def run_preflight_token_consolidation(self, session: Session) -> None:
        """Give token consolidation a short head start, then continue in background if needed."""
        task = self.ensure_background_token_consolidation(session)
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self.loop._PREFLIGHT_CONSOLIDATION_BUDGET_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Token consolidation still running for {} after {:.1f}s; continuing in background",
                session.key,
                self.loop._PREFLIGHT_CONSOLIDATION_BUDGET_SECONDS,
            )
        except Exception:
            logger.exception("Preflight token consolidation failed for {}", session.key)

    def stop(self) -> None:
        """Stop the agent loop."""
        self.loop._running = False
        logger.info("Agent loop stopping")
