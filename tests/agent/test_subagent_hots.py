"""Tests for SubagentManager HOTS hooks (inject + per-task cancel)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hahobot.agent.hook import AgentHookContext
from hahobot.agent.subagent import SubagentManager, _SubagentHook
from hahobot.bus.queue import MessageBus


def _build_manager(tmp_path: Path) -> SubagentManager:
    provider = MagicMock()
    provider.get_default_model.return_value = "default-model"
    return SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4096,
        model="default-model",
    )


def _context(messages: list[dict[str, object]]) -> AgentHookContext:
    return AgentHookContext(iteration=0, messages=messages)


def test_inject_message_returns_false_for_unknown_task(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    assert manager.inject_message("nope", "hint") is False


def test_inject_message_rejects_blank_content(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    manager._running_tasks["abc12345"] = MagicMock()
    assert manager.inject_message("abc12345", "   ") is False
    assert manager.pending_injections("abc12345") == 0


def test_inject_message_queues_for_running_task(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    manager._running_tasks["abc12345"] = MagicMock()
    assert manager.inject_message("abc12345", "first hint") is True
    assert manager.inject_message("abc12345", "second hint") is True
    assert manager.pending_injections("abc12345") == 2


@pytest.mark.asyncio
async def test_hook_drains_queue_into_context_messages(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    manager._running_tasks["abc12345"] = MagicMock()
    manager.inject_message("abc12345", "first hint")
    manager.inject_message("abc12345", "second hint")
    hook = _SubagentHook("abc12345", injection_pop=manager._pop_injections)
    messages: list[dict[str, object]] = [{"role": "user", "content": "do thing"}]
    ctx = _context(messages)
    await hook.before_iteration(ctx)
    assert messages[-2]["role"] == "system"
    assert "first hint" in messages[-2]["content"]
    assert messages[-1]["role"] == "system"
    assert "second hint" in messages[-1]["content"]
    # Queue cleared after drain
    assert manager.pending_injections("abc12345") == 0

    # Second drain with no pending = no-op
    await hook.before_iteration(ctx)
    assert len([m for m in messages if m["role"] == "system"]) == 2


@pytest.mark.asyncio
async def test_cancel_task_stops_running_task(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)

    async def _long_runner() -> None:
        await asyncio.sleep(5)

    real_task = asyncio.create_task(_long_runner())
    manager._running_tasks["xyz98765"] = real_task

    ok = await manager.cancel_task("xyz98765")
    assert ok is True
    assert real_task.cancelled()


@pytest.mark.asyncio
async def test_cancel_task_returns_false_for_unknown(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    assert await manager.cancel_task("nope") is False


@pytest.mark.asyncio
async def test_inject_cleared_when_task_finishes(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)

    async def _short_runner() -> None:
        await asyncio.sleep(0)

    real_task = asyncio.create_task(_short_runner())
    manager._running_tasks["short001"] = real_task

    def _cleanup(_: asyncio.Task) -> None:
        manager._running_tasks.pop("short001", None)
        manager._injections.pop("short001", None)

    real_task.add_done_callback(_cleanup)
    manager.inject_message("short001", "queued before task ends")
    assert manager.pending_injections("short001") == 1

    await real_task
    # Cleanup runs after task completes
    await asyncio.sleep(0)
    assert manager.pending_injections("short001") == 0
