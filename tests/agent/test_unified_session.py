"""Tests for unified_session routing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hahobot.bus.events import InboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.command.builtin import cmd_stop
from hahobot.command.router import CommandContext
from hahobot.config.schema import AgentDefaults, Config


def _make_loop(tmp_path: Path, *, unified_session: bool = False):
    from hahobot.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    with (
        patch("hahobot.agent.loop.ContextBuilder"),
        patch("hahobot.agent.loop.SessionManager"),
        patch("hahobot.agent.loop.SubagentManager") as mock_sub_mgr,
    ):
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path,
            unified_session=unified_session,
        )
    return loop


def _make_msg(
    *,
    channel: str = "telegram",
    chat_id: str = "111",
    session_key_override: str | None = None,
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        chat_id=chat_id,
        sender_id="user1",
        content="hello",
        session_key_override=session_key_override,
    )


@pytest.mark.asyncio
async def test_unified_session_rewrites_key_to_unified_default(tmp_path: Path):
    from hahobot.agent.loop import UNIFIED_SESSION_KEY

    loop = _make_loop(tmp_path, unified_session=True)
    captured: list[str] = []

    async def fake_process(msg, **_kwargs):
        captured.append(msg.session_key)
        return None

    loop._process_message = fake_process  # type: ignore[method-assign]
    await loop._dispatch(_make_msg())

    assert captured == [UNIFIED_SESSION_KEY]


@pytest.mark.asyncio
async def test_unified_session_respects_existing_override(tmp_path: Path):
    loop = _make_loop(tmp_path, unified_session=True)
    captured: list[str] = []

    async def fake_process(msg, **_kwargs):
        captured.append(msg.session_key)
        return None

    loop._process_message = fake_process  # type: ignore[method-assign]
    await loop._dispatch(_make_msg(session_key_override="telegram:thread:42"))

    assert captured == ["telegram:thread:42"]


def test_agent_defaults_unified_session_default_is_false():
    assert AgentDefaults().unified_session is False


def test_config_serializes_unified_session_as_camel_case():
    data = Config().model_dump(mode="json", by_alias=True)
    assert data["agents"]["defaults"]["unifiedSession"] is False


@pytest.mark.asyncio
async def test_cmd_stop_uses_effective_key_in_unified_mode(tmp_path: Path):
    from hahobot.agent.loop import UNIFIED_SESSION_KEY

    loop = _make_loop(tmp_path, unified_session=True)

    async def long_running():
        await asyncio.sleep(10)

    task = asyncio.create_task(long_running())
    loop._active_tasks[UNIFIED_SESSION_KEY] = [task]

    msg = InboundMessage(
        channel="discord",
        chat_id="999",
        sender_id="user1",
        content="/stop",
        session_key_override=UNIFIED_SESSION_KEY,
    )
    ctx = CommandContext(msg=msg, session=None, key=UNIFIED_SESSION_KEY, raw="/stop", loop=loop)
    result = await cmd_stop(ctx)

    assert "Stopped 1 task" in result.content
    assert task.cancelled() or task.done()
