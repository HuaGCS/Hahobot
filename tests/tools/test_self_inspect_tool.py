from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from hahobot.agent.loop import AgentLoop
from hahobot.bus.queue import MessageBus


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = MagicMock(max_tokens=4096)
    return provider


@pytest.mark.asyncio
async def test_self_inspect_reports_runtime_session_and_subagents(tmp_path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
    )
    session = loop.sessions.get_or_create("unified:default")
    session.add_message("user", "hello")
    session.add_message("assistant", "hi")
    session.metadata["working_checkpoint"] = {
        "goal": "Investigate the active runtime",
        "status": "running",
        "current_step": "Collect runtime context",
        "next_step": "Report the state",
        "recent_tools": ["read_file"],
    }
    loop.sessions.save(session)

    task = asyncio.create_task(asyncio.sleep(60))
    loop.subagents._running_tasks["sub-1"] = task
    loop.subagents._task_meta["sub-1"] = {
        "task_id": "sub-1",
        "label": "inspect runtime",
        "mode": "explore",
        "origin_channel": "telegram/main",
        "origin_chat_id": "chat-1",
        "session_key": "unified:default",
    }

    tool = loop.tools.get("self_inspect")
    assert tool is not None
    tool.set_context("telegram/main", "chat-1", "unified:default", "coder")

    try:
        payload = json.loads(await tool.execute())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert payload["runtime"]["model"] == "test-model"
    assert payload["runtime"]["workspace"] == str(tmp_path)

    assert payload["session"]["key"] == "unified:default"
    assert payload["session"]["channel"] == "telegram/main"
    assert payload["session"]["chat_id"] == "chat-1"
    assert payload["session"]["persona"] == "coder"
    assert payload["session"]["message_count"] == 2
    assert payload["session"]["working_checkpoint"]["current_step"] == "Collect runtime context"

    assert "self_inspect" in payload["tools"]["registered"]
    assert "notebook_edit" in payload["tools"]["registered"]

    assert payload["subagents"]["running_count"] == 1
    assert payload["subagents"]["current_session_running_count"] == 1
    assert payload["subagents"]["current_session_running"][0]["task_id"] == "sub-1"


@pytest.mark.asyncio
async def test_self_inspect_can_return_one_section(tmp_path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
    )
    tool = loop.tools.get("self_inspect")
    assert tool is not None

    payload = json.loads(await tool.execute(section="runtime"))
    assert payload["model"] == "test-model"
    assert payload["workspace"] == str(tmp_path)
