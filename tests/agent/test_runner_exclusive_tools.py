from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from hahobot.agent.runner import AgentRunner, AgentRunSpec
from hahobot.agent.tools.base import Tool
from hahobot.agent.tools.registry import ToolRegistry
from hahobot.config.schema import AgentDefaults
from hahobot.providers.base import ToolCallRequest

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


class _DelayTool(Tool):
    def __init__(
        self,
        name: str,
        *,
        delay: float,
        read_only: bool,
        shared_events: list[str],
        exclusive: bool = False,
    ) -> None:
        self._name = name
        self._delay = delay
        self._read_only = read_only
        self._shared_events = shared_events
        self._exclusive = exclusive

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def exclusive(self) -> bool:
        return self._exclusive

    async def execute(self, **kwargs):
        self._shared_events.append(f"start:{self._name}")
        await asyncio.sleep(self._delay)
        self._shared_events.append(f"end:{self._name}")
        return self._name


@pytest.mark.asyncio
async def test_runner_does_not_batch_exclusive_read_only_tools() -> None:
    tools = ToolRegistry()
    shared_events: list[str] = []
    read_a = _DelayTool("read_a", delay=0.03, read_only=True, shared_events=shared_events)
    read_b = _DelayTool("read_b", delay=0.03, read_only=True, shared_events=shared_events)
    ddg_like = _DelayTool(
        "ddg_like",
        delay=0.01,
        read_only=True,
        shared_events=shared_events,
        exclusive=True,
    )
    tools.register(read_a)
    tools.register(ddg_like)
    tools.register(read_b)

    runner = AgentRunner(MagicMock())
    await runner._execute_tools(
        AgentRunSpec(
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            concurrent_tools=True,
        ),
        [
            ToolCallRequest(id="ro1", name="read_a", arguments={}),
            ToolCallRequest(id="ddg1", name="ddg_like", arguments={}),
            ToolCallRequest(id="ro2", name="read_b", arguments={}),
        ],
        {},
    )

    assert shared_events[0] == "start:read_a"
    assert shared_events.index("end:read_a") < shared_events.index("start:ddg_like")
    assert shared_events.index("end:ddg_like") < shared_events.index("start:read_b")
