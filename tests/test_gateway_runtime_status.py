from __future__ import annotations

import pytest

from hahobot.agent.hook import AgentHookContext
from hahobot.gateway.runtime_status import GatewayRuntimeStatusTracker, GatewayStatusHook
from hahobot.providers.base import ToolCallRequest
from hahobot.star_office import StarOfficeSnapshot


def _idle_snapshot() -> StarOfficeSnapshot:
    return StarOfficeSnapshot(
        state="idle",
        detail="Ready",
        updated_at="2026-04-07T00:00:00Z",
        updated_at_ms=1,
        active_runs=0,
    )


@pytest.mark.asyncio
async def test_gateway_status_hook_tracks_latest_completed_task() -> None:
    tracker = GatewayRuntimeStatusTracker(model="openrouter/sonnet")
    hook = GatewayStatusHook(tracker)
    context = AgentHookContext(
        iteration=0,
        messages=[{"role": "user", "content": "Review the latest incident report"}],
    )

    await hook.before_iteration(context)
    context.tool_calls = [ToolCallRequest(id="call_1", name="grep", arguments={"pattern": "incident"})]
    await hook.before_execute_tools(context)
    context.final_content = "Incident report reviewed."
    context.stop_reason = "completed"
    await hook.after_iteration(context)

    snapshot = tracker.snapshot(_idle_snapshot())

    assert snapshot.model == "openrouter/sonnet"
    assert snapshot.recent_task is not None
    assert snapshot.recent_task.summary == "Review the latest incident report"
    assert snapshot.recent_task.status == "ok"
    assert snapshot.recent_task.current_step == "Final response delivered"
    assert snapshot.recent_task.next_step == ""
    assert snapshot.recent_task.response_preview == "Incident report reviewed."


@pytest.mark.asyncio
async def test_gateway_status_hook_tracks_current_and_next_steps_while_running() -> None:
    tracker = GatewayRuntimeStatusTracker(model="openrouter/sonnet")
    hook = GatewayStatusHook(tracker)
    context = AgentHookContext(
        iteration=0,
        messages=[{"role": "user", "content": "Audit the workspace for stale configs"}],
        tool_calls=[
            ToolCallRequest(id="call_1", name="glob", arguments={"pattern": "*.json"}),
            ToolCallRequest(id="call_2", name="read_file", arguments={"path": "config.json"}),
        ],
    )

    await hook.before_iteration(context)
    await hook.before_execute_tools(context)

    running_snapshot = tracker.snapshot(_idle_snapshot())

    assert running_snapshot.recent_task is not None
    assert running_snapshot.recent_task.status == "running"
    assert running_snapshot.recent_task.current_step == "Running tools: glob, read_file"
    assert running_snapshot.recent_task.next_step == "Wait for tool results"

    context.tool_events = [
        {"name": "glob", "status": "ok", "detail": "2 files"},
        {"name": "read_file", "status": "ok", "detail": "loaded"},
    ]
    await hook.after_iteration(context)

    updated_snapshot = tracker.snapshot(_idle_snapshot())

    assert updated_snapshot.recent_task is not None
    assert updated_snapshot.recent_task.status == "running"
    assert updated_snapshot.recent_task.current_step == "Completed tools: glob, read_file"
    assert updated_snapshot.recent_task.next_step == "Prepare the final response"
