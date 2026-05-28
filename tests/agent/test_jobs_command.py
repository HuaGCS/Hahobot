"""Tests for the /jobs slash command (channel-side HOTS)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hahobot.bus.events import InboundMessage
from hahobot.command.builtin import cmd_jobs
from hahobot.command.router import CommandContext


def _ctx(
    *,
    args: str = "",
    session_key: str = "cli:direct",
    snapshot: list[dict[str, str]] | None = None,
    cross_session_snapshot: list[dict[str, str]] | None = None,
    inject_result: bool = True,
    cancel_result: bool = True,
) -> tuple[CommandContext, MagicMock]:
    manager = MagicMock()

    def _snapshot(session_key: str | None = None) -> list[dict[str, str]]:
        if session_key is None:
            return cross_session_snapshot or snapshot or []
        return snapshot or []

    manager.running_tasks_snapshot = MagicMock(side_effect=_snapshot)
    manager.inject_message = MagicMock(return_value=inject_result)
    manager.cancel_task = AsyncMock(return_value=cancel_result)

    loop = MagicMock()
    loop.subagents = manager

    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content=f"/jobs {args}".strip(),
    )
    ctx = CommandContext(
        msg=msg,
        session=None,
        key=session_key,
        raw=f"/jobs {args}".strip(),
        args=args,
        loop=loop,
    )
    return ctx, manager


@pytest.mark.asyncio
async def test_list_empty_returns_friendly_message() -> None:
    ctx, manager = _ctx(snapshot=[])
    response = await cmd_jobs(ctx)
    assert "No running subagents" in response.content
    manager.running_tasks_snapshot.assert_called_once_with(session_key="cli:direct")


@pytest.mark.asyncio
async def test_list_shows_running_tasks_for_session() -> None:
    snap = [
        {
            "task_id": "abc12345",
            "label": "image extract",
            "mode": "explore",
            "model": "openai/gpt-4.1-mini",
            "session_key": "cli:direct",
        },
        {
            "task_id": "def67890",
            "label": "deep audit",
            "mode": "implement",
            "model": "anthropic/claude-opus-4-5",
            "session_key": "cli:direct",
        },
    ]
    ctx, _manager = _ctx(snapshot=snap)
    response = await cmd_jobs(ctx)
    assert "2 running subagent(s)" in response.content
    assert "abc12345" in response.content
    assert "def67890" in response.content
    assert "image extract" in response.content
    assert "openai/gpt-4.1-mini" in response.content


@pytest.mark.asyncio
async def test_explicit_list_subcommand_works() -> None:
    ctx, manager = _ctx(args="list", snapshot=[])
    response = await cmd_jobs(ctx)
    assert "No running subagents" in response.content
    manager.running_tasks_snapshot.assert_called_once_with(session_key="cli:direct")


@pytest.mark.asyncio
async def test_inject_routes_to_manager_when_task_owned() -> None:
    snap = [{"task_id": "abc12345", "label": "x", "mode": "explore", "model": "m"}]
    ctx, manager = _ctx(args="inject abc12345 try this hint", snapshot=snap)
    response = await cmd_jobs(ctx)
    manager.inject_message.assert_called_once_with("abc12345", "try this hint")
    assert "Queued injection" in response.content


@pytest.mark.asyncio
async def test_inject_rejects_task_not_in_session() -> None:
    ctx, manager = _ctx(args="inject other999 hello", snapshot=[])
    response = await cmd_jobs(ctx)
    manager.inject_message.assert_not_called()
    assert "No running subagent" in response.content


@pytest.mark.asyncio
async def test_inject_without_message_returns_usage() -> None:
    snap = [{"task_id": "abc12345", "label": "x", "mode": "explore", "model": "m"}]
    ctx, manager = _ctx(args="inject abc12345", snapshot=snap)
    response = await cmd_jobs(ctx)
    manager.inject_message.assert_not_called()
    assert "Usage:" in response.content


@pytest.mark.asyncio
async def test_cancel_routes_to_manager_when_task_owned() -> None:
    snap = [{"task_id": "abc12345", "label": "x", "mode": "explore", "model": "m"}]
    ctx, manager = _ctx(args="cancel abc12345", snapshot=snap)
    response = await cmd_jobs(ctx)
    manager.cancel_task.assert_awaited_once_with("abc12345")
    assert "Cancelled task abc12345" in response.content


@pytest.mark.asyncio
async def test_cancel_rejects_task_not_in_session() -> None:
    ctx, manager = _ctx(args="cancel other999", snapshot=[])
    response = await cmd_jobs(ctx)
    manager.cancel_task.assert_not_awaited()
    assert "No running subagent" in response.content


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    ctx, _manager = _ctx(args="wat")
    response = await cmd_jobs(ctx)
    assert "Usage:" in response.content


@pytest.mark.asyncio
async def test_returns_friendly_message_when_no_subagent_runtime() -> None:
    msg = InboundMessage(channel="cli", sender_id="u", chat_id="d", content="/jobs")
    loop = MagicMock()
    loop.subagents = None
    ctx = CommandContext(msg=msg, session=None, key="cli:direct", raw="/jobs", args="", loop=loop)
    response = await cmd_jobs(ctx)
    assert "not attached" in response.content


@pytest.mark.asyncio
async def test_user_cannot_inject_into_other_sessions_task() -> None:
    # snapshot for this session is empty, but the task exists in another session
    ctx, manager = _ctx(
        args="inject zzz99999 sneaky",
        snapshot=[],
        cross_session_snapshot=[
            {"task_id": "zzz99999", "session_key": "telegram:other-user"}
        ],
    )
    response = await cmd_jobs(ctx)
    manager.inject_message.assert_not_called()
    assert "No running subagent" in response.content
