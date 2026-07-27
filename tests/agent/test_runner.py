"""Tests for the shared agent runner and its integration contracts."""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hahobot.agent.tools.base import Tool
from hahobot.agent.tools.registry import ToolRegistry
from hahobot.config.schema import AgentDefaults
from hahobot.providers.base import LLMResponse, ToolCallRequest

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _make_loop(tmp_path):
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    with (
        patch("hahobot.agent.loop.ContextBuilder"),
        patch("hahobot.agent.loop.SessionManager"),
        patch("hahobot.agent.loop.SubagentManager") as mock_sub_mgr,
    ):
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        return AgentLoop(bus=bus, provider=provider, workspace=tmp_path)


@pytest.mark.asyncio
async def test_runner_preserves_reasoning_fields_and_tool_results():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    captured_second_call: list[dict] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="thinking",
                tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
                reasoning_content="hidden reasoning",
                thinking_blocks=[{"type": "thinking", "thinking": "step"}],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        captured_second_call[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="tool result")

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "do task"},
            ],
            tools=tools,
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.final_content == "done"
    assert result.tools_used == ["list_dir"]
    assert result.tool_events == [{"name": "list_dir", "status": "ok", "detail": "tool result"}]

    assistant_messages = [
        msg
        for msg in captured_second_call
        if msg.get("role") == "assistant" and msg.get("tool_calls")
    ]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["reasoning_content"] == "hidden reasoning"
    assert assistant_messages[0]["thinking_blocks"] == [{"type": "thinking", "thinking": "step"}]
    assert any(
        msg.get("role") == "tool" and msg.get("content") == "tool result"
        for msg in captured_second_call
    )


@pytest.mark.asyncio
async def test_runner_calls_hooks_in_order():
    from hahobot.agent.hook import AgentHook, AgentHookContext
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = {"n": 0}
    events: list[tuple] = []

    async def chat_with_retry(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="thinking",
                tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
            )
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="tool result")

    class RecordingHook(AgentHook):
        async def before_iteration(self, context: AgentHookContext) -> None:
            events.append(("before_iteration", context.iteration))

        async def before_execute_tools(self, context: AgentHookContext) -> None:
            events.append(
                (
                    "before_execute_tools",
                    context.iteration,
                    [tc.name for tc in context.tool_calls],
                )
            )

        async def after_iteration(self, context: AgentHookContext) -> None:
            events.append(
                (
                    "after_iteration",
                    context.iteration,
                    context.final_content,
                    list(context.tool_results),
                    list(context.tool_events),
                    context.stop_reason,
                )
            )

        def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
            events.append(("finalize_content", context.iteration, content))
            return content.upper() if content else content

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            hook=RecordingHook(),
        )
    )

    assert result.final_content == "DONE"
    assert events == [
        ("before_iteration", 0),
        ("before_execute_tools", 0, ["list_dir"]),
        (
            "after_iteration",
            0,
            None,
            ["tool result"],
            [{"name": "list_dir", "status": "ok", "detail": "tool result"}],
            None,
        ),
        ("before_iteration", 1),
        ("finalize_content", 1, "done"),
        ("after_iteration", 1, "DONE", [], [], "completed"),
    ]


@pytest.mark.asyncio
async def test_runner_streaming_hook_receives_deltas_and_end_signal():
    from hahobot.agent.hook import AgentHook, AgentHookContext
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    streamed: list[str] = []
    endings: list[bool] = []

    async def chat_stream_with_retry(*, on_content_delta, **kwargs):
        await on_content_delta("he")
        await on_content_delta("llo")
        return LLMResponse(content="hello", tool_calls=[], usage={})

    provider.chat_stream_with_retry = chat_stream_with_retry
    provider.chat_with_retry = AsyncMock()
    tools = MagicMock()
    tools.get_definitions.return_value = []

    class StreamingHook(AgentHook):
        def wants_streaming(self) -> bool:
            return True

        async def on_stream(self, context: AgentHookContext, delta: str) -> None:
            streamed.append(delta)

        async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
            endings.append(resuming)

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            hook=StreamingHook(),
        )
    )

    assert result.final_content == "hello"
    assert streamed == ["he", "llo"]
    assert endings == [False]
    provider.chat_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_returns_max_iterations_fallback():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(
            content="still working",
            tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
        )
    )
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="tool result")

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=2,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.stop_reason == "max_iterations"
    assert result.final_content == (
        "I reached the maximum number of tool call iterations (2) "
        "without completing the task. You can try breaking the task into smaller steps."
    )
    assert result.messages[-1]["role"] == "assistant"
    assert result.messages[-1]["content"] == result.final_content


@pytest.mark.asyncio
async def test_runner_returns_structured_tool_error():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(
            content="working",
            tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
        )
    )
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=RuntimeError("boom"))

    runner = AgentRunner(provider)

    result = await runner.run(
        AgentRunSpec(
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=2,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            fail_on_tool_error=True,
        )
    )

    assert result.stop_reason == "tool_error"
    assert result.error == "Error: RuntimeError: boom"
    assert result.tool_events == [{"name": "list_dir", "status": "error", "detail": "boom"}]


@pytest.mark.asyncio
async def test_runner_persists_large_tool_results_for_follow_up_calls(tmp_path):
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    captured_second_call: list[dict] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="working",
                tool_calls=[
                    ToolCallRequest(id="call_big", name="list_dir", arguments={"path": "."})
                ],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        captured_second_call[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="x" * 20_000)

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do task"}],
            tools=tools,
            model="test-model",
            max_iterations=2,
            workspace=tmp_path,
            session_key="test:runner",
            max_tool_result_chars=2048,
        )
    )

    assert result.final_content == "done"
    tool_message = next(msg for msg in captured_second_call if msg.get("role") == "tool")
    assert "[tool output persisted]" in tool_message["content"]
    assert "tool-results" in tool_message["content"]
    assert (tmp_path / ".hahobot" / "tool-results" / "test_runner" / "call_big.txt").exists()


def test_persist_tool_result_prunes_old_session_buckets(tmp_path):
    from hahobot.utils.helpers import maybe_persist_tool_result

    root = tmp_path / ".hahobot" / "tool-results"
    old_bucket = root / "old_session"
    recent_bucket = root / "recent_session"
    old_bucket.mkdir(parents=True)
    recent_bucket.mkdir(parents=True)
    (old_bucket / "old.txt").write_text("old", encoding="utf-8")
    (recent_bucket / "recent.txt").write_text("recent", encoding="utf-8")

    stale = time.time() - (8 * 24 * 60 * 60)
    os.utime(old_bucket, (stale, stale))
    os.utime(old_bucket / "old.txt", (stale, stale))

    persisted = maybe_persist_tool_result(
        tmp_path,
        "current:session",
        "call_big",
        "x" * 5000,
        max_chars=64,
    )

    assert "[tool output persisted]" in persisted
    assert not old_bucket.exists()
    assert recent_bucket.exists()
    assert (root / "current_session" / "call_big.txt").exists()


def test_persist_tool_result_leaves_no_temp_files(tmp_path):
    from hahobot.utils.helpers import maybe_persist_tool_result

    root = tmp_path / ".hahobot" / "tool-results"
    maybe_persist_tool_result(
        tmp_path,
        "current:session",
        "call_big",
        "x" * 5000,
        max_chars=64,
    )

    assert (root / "current_session" / "call_big.txt").exists()
    assert list((root / "current_session").glob("*.tmp")) == []


def test_persist_tool_result_logs_cleanup_failures(monkeypatch, tmp_path):
    from hahobot.utils.helpers import maybe_persist_tool_result

    warnings: list[str] = []

    monkeypatch.setattr(
        "hahobot.utils.helpers._cleanup_tool_result_buckets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
    )
    monkeypatch.setattr(
        "hahobot.utils.helpers.logger.warning",
        lambda message, *args: warnings.append(message.format(*args)),
    )

    persisted = maybe_persist_tool_result(
        tmp_path,
        "current:session",
        "call_big",
        "x" * 5000,
        max_chars=64,
    )

    assert "[tool output persisted]" in persisted
    assert warnings and "Failed to clean stale tool result buckets" in warnings[0]


@pytest.mark.asyncio
async def test_runner_replaces_empty_tool_result_with_marker():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    captured_second_call: list[dict] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="working",
                tool_calls=[ToolCallRequest(id="call_1", name="noop", arguments={})],
                usage={},
            )
        captured_second_call[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="")

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do task"}],
            tools=tools,
            model="test-model",
            max_iterations=2,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.final_content == "done"
    tool_message = next(msg for msg in captured_second_call if msg.get("role") == "tool")
    assert tool_message["content"] == "(noop completed with no output)"


@pytest.mark.asyncio
async def test_runner_uses_raw_messages_when_context_governance_fails():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    captured_messages: list[dict] = []

    async def chat_with_retry(*, messages, **kwargs):
        captured_messages[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    initial_messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ]

    runner = AgentRunner(provider)
    runner._snip_history = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    result = await runner.run(
        AgentRunSpec(
            initial_messages=initial_messages,
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.final_content == "done"
    assert captured_messages == initial_messages


@pytest.mark.asyncio
async def test_runner_retries_empty_final_response_with_summary_prompt():
    """Empty responses get 2 silent retries before finalization kicks in."""
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    calls: list[dict] = []

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        calls.append({"messages": messages, "tools": tools})
        if len(calls) <= 2:
            return LLMResponse(
                content=None,
                tool_calls=[],
                usage={"prompt_tokens": 5, "completion_tokens": 1},
            )
        return LLMResponse(
            content="final answer",
            tool_calls=[],
            usage={"prompt_tokens": 3, "completion_tokens": 7},
        )

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do task"}],
            tools=tools,
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.final_content == "final answer"
    # 2 silent retries (iterations 0,1) + finalization on iteration 1
    assert len(calls) == 3
    assert calls[0]["tools"] is not None
    assert calls[1]["tools"] is not None
    assert calls[2]["tools"] is None
    assert result.usage["prompt_tokens"] == 13
    assert result.usage["completion_tokens"] == 9


@pytest.mark.asyncio
async def test_runner_uses_specific_message_after_empty_finalization_retry():
    """After silent retries + finalization all return empty, stop_reason is empty_final_response."""
    from hahobot.agent.runner import AgentRunner, AgentRunSpec
    from hahobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

    provider = MagicMock()

    async def chat_with_retry(*, messages, **kwargs):
        return LLMResponse(content=None, tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do task"}],
            tools=tools,
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.final_content == EMPTY_FINAL_RESPONSE_MESSAGE
    assert result.stop_reason == "empty_final_response"


@pytest.mark.asyncio
async def test_runner_empty_response_does_not_break_tool_chain():
    """An empty intermediate response must not kill an ongoing tool chain.

    Sequence: tool_call → empty → tool_call → final text.
    The runner should recover via silent retry and complete normally.
    """
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = 0

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(id="tc1", name="read_file", arguments={"path": "a.txt"})
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        if call_count == 2:
            return LLMResponse(
                content=None, tool_calls=[], usage={"prompt_tokens": 10, "completion_tokens": 1}
            )
        if call_count == 3:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(id="tc2", name="read_file", arguments={"path": "b.txt"})
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        return LLMResponse(
            content="Here are the results.",
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 10},
        )

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry

    async def fake_tool(name, args, **kw):
        return "file content"

    tool_registry = MagicMock()
    tool_registry.get_definitions.return_value = [
        {"type": "function", "function": {"name": "read_file"}}
    ]
    tool_registry.execute = AsyncMock(side_effect=fake_tool)

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "read both files"}],
            tools=tool_registry,
            model="test-model",
            max_iterations=10,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.final_content == "Here are the results."
    assert result.stop_reason == "completed"
    assert call_count == 4
    assert "read_file" in result.tools_used


def test_snip_history_drops_orphaned_tool_results_from_trimmed_slice(monkeypatch):
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    tools = MagicMock()
    tools.get_definitions.return_value = []
    runner = AgentRunner(provider)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old user"},
        {
            "role": "assistant",
            "content": "tool call",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "ls", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "tool output"},
        {"role": "assistant", "content": "after tool"},
    ]
    spec = AgentRunSpec(
        initial_messages=messages,
        tools=tools,
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        context_window_tokens=2000,
        context_block_limit=100,
    )

    monkeypatch.setattr(
        "hahobot.agent.runner.estimate_prompt_tokens_chain", lambda *_args, **_kwargs: (500, None)
    )
    token_sizes = {
        "old user": 120,
        "tool call": 120,
        "tool output": 40,
        "after tool": 40,
        "system": 0,
    }
    monkeypatch.setattr(
        "hahobot.agent.runner.estimate_message_tokens",
        lambda msg: token_sizes.get(str(msg.get("content")), 40),
    )

    trimmed = runner._snip_history(spec, messages)

    assert trimmed == [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "after tool"},
    ]


@pytest.mark.asyncio
async def test_runner_keeps_going_when_tool_result_persistence_fails():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    captured_second_call: list[dict] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="working",
                tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        captured_second_call[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="tool result")

    runner = AgentRunner(provider)
    with patch(
        "hahobot.agent.runner.maybe_persist_tool_result", side_effect=RuntimeError("disk full")
    ):
        result = await runner.run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "do task"}],
                tools=tools,
                model="test-model",
                max_iterations=2,
                max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            )
        )

    assert result.final_content == "done"
    tool_message = next(msg for msg in captured_second_call if msg.get("role") == "tool")
    assert tool_message["content"] == "tool result"


class _DelayTool(Tool):
    def __init__(self, name: str, *, delay: float, read_only: bool, shared_events: list[str]):
        self._name = name
        self._delay = delay
        self._read_only = read_only
        self._shared_events = shared_events

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

    async def execute(self, **kwargs):
        self._shared_events.append(f"start:{self._name}")
        await asyncio.sleep(self._delay)
        self._shared_events.append(f"end:{self._name}")
        return self._name


@pytest.mark.asyncio
async def test_runner_batches_read_only_tools_before_exclusive_work():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    tools = ToolRegistry()
    shared_events: list[str] = []
    read_a = _DelayTool("read_a", delay=0.05, read_only=True, shared_events=shared_events)
    read_b = _DelayTool("read_b", delay=0.05, read_only=True, shared_events=shared_events)
    write_a = _DelayTool("write_a", delay=0.01, read_only=False, shared_events=shared_events)
    tools.register(read_a)
    tools.register(read_b)
    tools.register(write_a)

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
            ToolCallRequest(id="ro2", name="read_b", arguments={}),
            ToolCallRequest(id="rw1", name="write_a", arguments={}),
        ],
        {},
    )

    assert shared_events[0:2] == ["start:read_a", "start:read_b"]
    assert "end:read_a" in shared_events and "end:read_b" in shared_events
    assert shared_events.index("end:read_a") < shared_events.index("start:write_a")
    assert shared_events.index("end:read_b") < shared_events.index("start:write_a")
    assert shared_events[-2:] == ["start:write_a", "end:write_a"]


@pytest.mark.asyncio
async def test_runner_blocks_repeated_external_fetches():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    captured_final_call: list[dict] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 3:
            return LLMResponse(
                content="working",
                tool_calls=[
                    ToolCallRequest(
                        id=f"call_{call_count['n']}",
                        name="web_fetch",
                        arguments={"url": "https://example.com"},
                    )
                ],
                usage={},
            )
        captured_final_call[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="page content")

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "research task"}],
            tools=tools,
            model="test-model",
            max_iterations=4,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.final_content == "done"
    assert tools.execute.await_count == 2
    blocked_tool_message = [
        msg
        for msg in captured_final_call
        if msg.get("role") == "tool" and msg.get("tool_call_id") == "call_3"
    ][0]
    assert "repeated external lookup blocked" in blocked_tool_message["content"]


@pytest.mark.asyncio
async def test_loop_max_iterations_message_stays_stable(tmp_path):
    loop = _make_loop(tmp_path)
    loop.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(
            content="working",
            tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
        )
    )
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.tools.execute = AsyncMock(return_value="ok")
    loop.max_iterations = 2

    final_content, _, _, _ = await loop._run_agent_loop([])

    assert final_content == (
        "I reached the maximum number of tool call iterations (2) "
        "without completing the task. You can try breaking the task into smaller steps."
    )


@pytest.mark.asyncio
async def test_loop_stream_filter_handles_think_only_prefix_without_crashing(tmp_path):
    loop = _make_loop(tmp_path)
    deltas: list[str] = []
    endings: list[bool] = []

    async def chat_stream_with_retry(*, on_content_delta, **kwargs):
        await on_content_delta("<think>hidden")
        await on_content_delta("</think>Hello")
        return LLMResponse(content="<think>hidden</think>Hello", tool_calls=[], usage={})

    loop.provider.chat_stream_with_retry = chat_stream_with_retry

    async def on_stream(delta: str) -> None:
        deltas.append(delta)

    async def on_stream_end(*, resuming: bool = False) -> None:
        endings.append(resuming)

    final_content, _, _, _ = await loop._run_agent_loop(
        [],
        on_stream=on_stream,
        on_stream_end=on_stream_end,
    )

    assert final_content == "Hello"
    assert deltas == ["Hello"]
    assert endings == [False]


@pytest.mark.asyncio
async def test_loop_length_recovery_keeps_one_visible_stream_and_supplements_terminal(tmp_path):
    loop = _make_loop(tmp_path)
    deltas: list[str] = []
    endings: list[tuple[bool, bool]] = []
    call_count = 0

    async def chat_stream_with_retry(*, on_content_delta, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await on_content_delta("first ")
            return LLMResponse(content="first ", finish_reason="length", usage={})
        return LLMResponse(content="second", finish_reason="stop", usage={})

    loop.provider.chat_stream_with_retry = chat_stream_with_retry

    async def on_stream(delta: str) -> None:
        deltas.append(delta)

    async def on_stream_end(
        *,
        resuming: bool = False,
        merge_next: bool,
    ) -> None:
        endings.append((resuming, merge_next))

    final_content, _, _, _ = await loop._run_agent_loop(
        [],
        on_stream=on_stream,
        on_stream_end=on_stream_end,
    )

    assert final_content == "first second"
    assert deltas == ["first", " ", "second"]
    assert "".join(deltas) == final_content
    assert endings == [(True, True), (False, False)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_deltas", "terminal_content", "expected_terminal_deltas"),
    [
        (("<think>hidden</think>",), "<think>hidden</think>suffix", ["suffix"]),
        (("sec",), "second", ["sec", "ond"]),
    ],
)
async def test_loop_length_recovery_supplements_hidden_or_partial_terminal_deltas(
    tmp_path,
    terminal_deltas: tuple[str, ...],
    terminal_content: str,
    expected_terminal_deltas: list[str],
) -> None:
    loop = _make_loop(tmp_path)
    streamed: list[str] = []
    call_count = 0

    async def chat_stream_with_retry(*, on_content_delta, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await on_content_delta("prefix ")
            return LLMResponse(content="prefix ", finish_reason="length", usage={})
        for delta in terminal_deltas:
            await on_content_delta(delta)
        return LLMResponse(content=terminal_content, finish_reason="stop", usage={})

    loop.provider.chat_stream_with_retry = chat_stream_with_retry

    async def on_stream(delta: str) -> None:
        streamed.append(delta)

    final_content, _, _, _ = await loop._run_agent_loop([], on_stream=on_stream)

    assert streamed == ["prefix", " ", *expected_terminal_deltas]
    assert "".join(streamed) == final_content == f"prefix {terminal_content.split('>')[-1]}"


@pytest.mark.asyncio
async def test_loop_retries_think_only_final_response(tmp_path):
    loop = _make_loop(tmp_path)
    call_count = {"n": 0}

    async def chat_with_retry(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(content="<think>hidden</think>", tool_calls=[], usage={})
        return LLMResponse(content="Recovered answer", tool_calls=[], usage={})

    loop.provider.chat_with_retry = chat_with_retry

    final_content, _, _, _ = await loop._run_agent_loop([])

    assert final_content == "Recovered answer"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_llm_error_not_appended_to_session_messages():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(
            content="429 rate limit exceeded",
            finish_reason="error",
            tool_calls=[],
            usage={},
        )
    )
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "hello"}],
            tools=tools,
            model="test-model",
            max_iterations=5,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.stop_reason == "error"
    assert result.final_content == "429 rate limit exceeded"
    assistant_msgs = [m for m in result.messages if m.get("role") == "assistant"]
    assert all("429" not in (m.get("content") or "") for m in assistant_msgs)


@pytest.mark.asyncio
async def test_streamed_flag_not_set_on_llm_error(tmp_path):
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.events import InboundMessage
    from hahobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    error_resp = LLMResponse(
        content="503 service unavailable",
        finish_reason="error",
        tool_calls=[],
        usage={},
    )
    loop.provider.chat_with_retry = AsyncMock(return_value=error_resp)
    loop.provider.chat_stream_with_retry = AsyncMock(return_value=error_resp)
    loop.tools.get_definitions = MagicMock(return_value=[])

    msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content="hi")
    result = await loop._process_message(
        msg,
        on_stream=AsyncMock(),
        on_stream_end=AsyncMock(),
    )

    assert result is not None
    assert "503" in result.content
    assert not result.metadata.get("_streamed")


@pytest.mark.asyncio
async def test_runner_tool_error_sets_final_content():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()

    async def chat_with_retry(*, messages, **kwargs):
        return LLMResponse(
            content="working",
            tool_calls=[ToolCallRequest(id="call_1", name="read_file", arguments={"path": "x"})],
            usage={},
        )

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=RuntimeError("boom"))

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do task"}],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            fail_on_tool_error=True,
        )
    )

    assert result.final_content == "Error: RuntimeError: boom"
    assert result.stop_reason == "tool_error"


@pytest.mark.asyncio
async def test_subagent_max_iterations_announces_existing_fallback(tmp_path, monkeypatch):
    from hahobot.agent.subagent import SubagentManager
    from hahobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(
            content="working",
            tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
        )
    )
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    mgr._announce_result = AsyncMock()

    async def fake_execute(self, **kwargs):
        return "tool result"

    monkeypatch.setattr("hahobot.agent.tools.filesystem.ListDirTool.execute", fake_execute)

    await mgr._run_subagent(
        "sub-1",
        "do task",
        "label",
        "implement",
        {"channel": "test", "chat_id": "c1"},
    )

    mgr._announce_result.assert_awaited_once()
    args = mgr._announce_result.await_args.args
    assert args[3] == "Task completed but no final response was generated."
    assert args[5] == "ok"


@pytest.mark.asyncio
async def test_runner_accumulates_usage_and_preserves_cached_tokens():
    """Runner should accumulate prompt/completion tokens across iterations
    and preserve cached_tokens from provider responses."""
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="thinking",
                tool_calls=[
                    ToolCallRequest(id="call_1", name="read_file", arguments={"path": "x"})
                ],
                usage={"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 80},
            )
        return LLMResponse(
            content="done",
            tool_calls=[],
            usage={"prompt_tokens": 200, "completion_tokens": 20, "cached_tokens": 150},
        )

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="file content")

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "do task"}],
            tools=tools,
            model="test-model",
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    # Usage should be accumulated across iterations
    assert result.usage["prompt_tokens"] == 300  # 100 + 200
    assert result.usage["completion_tokens"] == 30  # 10 + 20
    assert result.usage["cached_tokens"] == 230  # 80 + 150


@pytest.mark.asyncio
async def test_runner_passes_cached_tokens_to_hook_context():
    """Hook context.usage should contain cached_tokens."""
    from hahobot.agent.hook import AgentHook, AgentHookContext
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    captured_usage: list[dict] = []

    class UsageHook(AgentHook):
        async def after_iteration(self, context: AgentHookContext) -> None:
            captured_usage.append(dict(context.usage))

    async def chat_with_retry(**kwargs):
        return LLMResponse(
            content="done",
            tool_calls=[],
            usage={"prompt_tokens": 200, "completion_tokens": 20, "cached_tokens": 150},
        )

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    await runner.run(
        AgentRunSpec(
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            hook=UsageHook(),
        )
    )

    assert len(captured_usage) == 1
    assert captured_usage[0]["cached_tokens"] == 150


# ---------------------------------------------------------------------------
# Length recovery (auto-continue on finish_reason == "length")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_length_recovery_continues_from_truncated_output():
    """When finish_reason is 'length', runner should insert a continuation
    prompt and retry, stitching partial outputs into the final result."""
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return LLMResponse(
                content=f"part{call_count['n']} ",
                finish_reason="length",
                usage={},
            )
        return LLMResponse(content="final", finish_reason="stop", usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "write a long essay"}],
            tools=tools,
            model="test-model",
            max_iterations=10,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.stop_reason == "completed"
    assert result.final_content == "part1 part2 final"
    assert call_count["n"] == 3
    recovery_prompts = [
        message["content"]
        for message in result.messages
        if message.get("role") == "user" and "<already_delivered_tail>" in message["content"]
    ]
    assert len(recovery_prompts) == 2
    assert "part1 " in recovery_prompts[0]
    assert "part2 " in recovery_prompts[1]


@pytest.mark.asyncio
async def test_length_recovery_streaming_calls_on_stream_end_with_resuming():
    """During length recovery with streaming, on_stream_end should be called
    with resuming=True so the hook knows the conversation is continuing."""
    from hahobot.agent.hook import AgentHook, AgentHookContext
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = {"n": 0}
    stream_end_calls: list[bool] = []
    merge_next_calls: list[bool] = []
    streamed: list[str] = []

    class StreamHook(AgentHook):
        def wants_streaming(self) -> bool:
            return True

        async def on_stream(self, context: AgentHookContext, delta: str) -> None:
            streamed.append(delta)

        async def on_stream_end(self, context: AgentHookContext, resuming: bool = False) -> None:
            stream_end_calls.append(resuming)
            merge_next_calls.append(context.stream_continues_current_message)

    async def chat_stream_with_retry(*, messages, on_content_delta=None, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(content="partial ", finish_reason="length", usage={})
        return LLMResponse(content="done", finish_reason="stop", usage={})

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=tools,
            model="test-model",
            max_iterations=10,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            hook=StreamHook(),
        )
    )

    assert len(stream_end_calls) == 2
    assert stream_end_calls[0] is True  # length recovery: resuming
    assert stream_end_calls[1] is False  # final response: done
    assert merge_next_calls == [True, False]
    assert streamed == ["partial ", "done"]
    assert result.final_content == "partial done"


@pytest.mark.asyncio
async def test_length_recovery_streams_non_delta_terminal_segment_once():
    from hahobot.agent.hook import AgentHook, AgentHookContext
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = 0
    streamed: list[str] = []

    async def chat_stream_with_retry(*, on_content_delta, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await on_content_delta("first ")
            return LLMResponse(content="first ", finish_reason="length", usage={})
        return LLMResponse(content="second", finish_reason="stop", usage={})

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    class StreamHook(AgentHook):
        def wants_streaming(self) -> bool:
            return True

        async def on_stream(self, context: AgentHookContext, delta: str) -> None:
            streamed.append(delta)

    result = await AgentRunner(provider).run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=tools,
            model="test-model",
            max_iterations=10,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            hook=StreamHook(),
        )
    )

    assert streamed == ["first ", "second"]
    assert result.final_content == "first second"


@pytest.mark.asyncio
async def test_length_recovery_gives_up_after_max_retries():
    """After _MAX_LENGTH_RECOVERIES attempts the runner should stop retrying."""
    from hahobot.agent.runner import _MAX_LENGTH_RECOVERIES, AgentRunner, AgentRunSpec

    provider = MagicMock()
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        return LLMResponse(
            content=f"chunk{call_count['n']}",
            finish_reason="length",
            usage={},
        )

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=tools,
            model="test-model",
            max_iterations=20,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert call_count["n"] == _MAX_LENGTH_RECOVERIES + 1
    assert result.final_content == "chunk1chunk2chunk3chunk4"


@pytest.mark.asyncio
async def test_length_recovery_chain_resets_after_tool_work():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    responses = iter(
        [
            LLMResponse(content="discarded partial ", finish_reason="length"),
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )

    async def chat_with_retry(**kwargs):
        return next(responses)

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="ok")

    result = await AgentRunner(provider).run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=tools,
            model="test-model",
            max_iterations=5,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        )
    )

    assert result.final_content == "done"


def test_length_recovery_prompt_anchors_only_the_last_64_characters():
    from hahobot.utils.runtime import build_length_recovery_message

    omitted_prefix = "OMITTED_PREFIX"
    tail = "x" * 64

    message = build_length_recovery_message(omitted_prefix + tail)

    assert message["role"] == "user"
    assert omitted_prefix not in message["content"]
    assert f"<already_delivered_tail>\n{tail}\n</already_delivered_tail>" in message["content"]


@pytest.mark.asyncio
async def test_length_recovery_max_iterations_streams_only_the_fallback_tail():
    from hahobot.agent.hook import AgentHook, AgentHookContext
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    provider.chat_stream_with_retry = AsyncMock(
        return_value=LLMResponse(content="partial", finish_reason="length")
    )
    tools = MagicMock()
    tools.get_definitions.return_value = []
    streamed: list[str] = []
    endings: list[tuple[bool, bool]] = []

    class StreamHook(AgentHook):
        def wants_streaming(self) -> bool:
            return True

        async def on_stream(self, context: AgentHookContext, delta: str) -> None:
            streamed.append(delta)

        async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
            endings.append((resuming, context.stream_continues_current_message))

    result = await AgentRunner(provider).run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_iterations_message="limit reached",
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            hook=StreamHook(),
        )
    )

    assert result.final_content == "partial\n\nlimit reached"
    assert streamed == ["partial", "\n\nlimit reached"]
    assert endings == [(True, True), (False, False)]


@pytest.mark.asyncio
async def test_dispatch_recovery_boundary_reuses_stream_id_until_final_end(tmp_path):
    from hahobot.bus.events import InboundMessage

    loop = _make_loop(tmp_path)
    msg = InboundMessage(
        channel="mock",
        sender_id="user",
        chat_id="chat",
        content="go",
        metadata={"_wants_stream": True},
    )
    on_stream, on_stream_end = loop._dispatch_runtime_manager()._stream_callbacks(msg)

    await on_stream("first ")
    await on_stream_end(resuming=True, merge_next=True)
    await on_stream("second")
    await on_stream_end(resuming=False)
    await on_stream("next segment")

    outbound = [await loop.bus.consume_outbound() for _ in range(5)]
    recovery_ids = {message.metadata["_stream_id"] for message in outbound[:4]}

    assert len(recovery_ids) == 1
    assert outbound[1].metadata["_merge_next"] is True
    assert outbound[1].metadata["_resuming"] is True
    assert "_merge_next" not in outbound[3].metadata
    assert outbound[4].metadata["_stream_id"] not in recovery_ids


# ---------------------------------------------------------------------------
# Backfill missing tool_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_missing_tool_results_inserts_error():
    """Orphaned tool_use (no matching tool_result) should get a synthetic error."""
    from hahobot.agent.runner import _BACKFILL_CONTENT, AgentRunner

    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "exec", "arguments": "{}"},
                },
                {
                    "id": "call_b",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "name": "exec", "content": "ok"},
    ]
    result = AgentRunner._backfill_missing_tool_results(messages)
    tool_msgs = [m for m in result if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    backfilled = [m for m in tool_msgs if m.get("tool_call_id") == "call_b"]
    assert len(backfilled) == 1
    assert backfilled[0]["content"] == _BACKFILL_CONTENT
    assert backfilled[0]["name"] == "read_file"


@pytest.mark.asyncio
async def test_backfill_noop_when_complete():
    """Complete message chains should not be modified."""
    from hahobot.agent.runner import AgentRunner

    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_x",
                    "type": "function",
                    "function": {"name": "exec", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_x", "name": "exec", "content": "done"},
        {"role": "assistant", "content": "all good"},
    ]
    result = AgentRunner._backfill_missing_tool_results(messages)
    assert result is messages  # same object — no copy


# ---------------------------------------------------------------------------
# Microcompact (stale tool result compaction)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_microcompact_replaces_old_tool_results():
    """Tool results beyond _MICROCOMPACT_KEEP_RECENT should be summarized."""
    from hahobot.agent.runner import _MICROCOMPACT_KEEP_RECENT, AgentRunner

    total = _MICROCOMPACT_KEEP_RECENT + 5
    long_content = "x" * 600
    messages: list[dict] = [{"role": "system", "content": "sys"}]
    for i in range(total):
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "name": "read_file",
                "content": long_content,
            }
        )

    result = AgentRunner._microcompact(messages)
    tool_msgs = [m for m in result if m.get("role") == "tool"]
    stale_count = total - _MICROCOMPACT_KEEP_RECENT
    compacted = [m for m in tool_msgs if "omitted from context" in str(m.get("content", ""))]
    preserved = [m for m in tool_msgs if m.get("content") == long_content]
    assert len(compacted) == stale_count
    assert len(preserved) == _MICROCOMPACT_KEEP_RECENT


@pytest.mark.asyncio
async def test_microcompact_preserves_short_results():
    """Short tool results (< _MICROCOMPACT_MIN_CHARS) should not be replaced."""
    from hahobot.agent.runner import _MICROCOMPACT_KEEP_RECENT, AgentRunner

    total = _MICROCOMPACT_KEEP_RECENT + 5
    messages: list[dict] = []
    for i in range(total):
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "exec", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "name": "exec",
                "content": "short",
            }
        )

    result = AgentRunner._microcompact(messages)
    assert result is messages  # no copy needed — all stale results are short


@pytest.mark.asyncio
async def test_microcompact_skips_non_compactable_tools():
    """Non-compactable tools (e.g. 'message') should never be replaced."""
    from hahobot.agent.runner import _MICROCOMPACT_KEEP_RECENT, AgentRunner

    total = _MICROCOMPACT_KEEP_RECENT + 5
    long_content = "y" * 1000
    messages: list[dict] = []
    for i in range(total):
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "message", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "name": "message",
                "content": long_content,
            }
        )

    result = AgentRunner._microcompact(messages)
    assert result is messages  # no compactable tools found


@pytest.mark.asyncio
async def test_runner_times_out_hung_provider_request(monkeypatch):
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    async def chat_with_retry(**kwargs):
        await asyncio.sleep(1)
        return LLMResponse(content="late")

    provider = MagicMock()
    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "hi"}],
            tools=tools,
            model="test-model",
            max_iterations=1,
            llm_timeout_s=0.01,
        )
    )

    assert result.stop_reason == "error"
    assert "timed out after" in (result.error or "")
    assert "timed out after" in (result.final_content or "")


@pytest.mark.asyncio
async def test_runner_timeout_can_be_disabled(monkeypatch):
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    async def chat_with_retry(**kwargs):
        await asyncio.sleep(0)
        return LLMResponse(content="ok")

    provider = MagicMock()
    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "hi"}],
            tools=tools,
            model="test-model",
            max_iterations=1,
            llm_timeout_s=0,
        )
    )

    assert result.final_content == "ok"


def test_runner_gives_streaming_requests_a_wider_wall_clock_timeout():
    from hahobot.agent.runner import AgentRunner, AgentRunSpec

    tools = MagicMock()
    short = AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=1,
        llm_timeout_s=10,
    )
    long = AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=1,
        llm_timeout_s=200,
    )
    disabled = AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=1,
        llm_timeout_s=0,
    )

    assert AgentRunner._request_timeout_s(short, streaming=False) == 10
    assert AgentRunner._request_timeout_s(short, streaming=True) == 300
    assert AgentRunner._request_timeout_s(long, streaming=True) == 400
    assert AgentRunner._request_timeout_s(disabled, streaming=True) is None
