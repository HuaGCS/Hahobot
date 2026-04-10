"""Tests for bridging agent hooks to external commands."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hahobot.agent.hook import AgentHookContext
from hahobot.agent.hook_bridge import (
    ExternalHookBridge,
    ExternalHookBridgeBlocked,
    ExternalHookBridgeError,
)
from hahobot.bus.events import InboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.providers.base import LLMResponse, ToolCallRequest


def _python_hook(script: str) -> list[str]:
    return [sys.executable, "-c", script]


def _context(tmp_path: Path) -> AgentHookContext:
    return AgentHookContext(
        iteration=1,
        messages=[{"role": "user", "content": "hello"}],
        workspace=tmp_path,
        session_key="cli:test",
        model="openai/gpt-4.1",
        request_messages=[{"role": "system", "content": "sys"}],
        response=LLMResponse(content="thinking", tool_calls=[]),
        usage={"prompt_tokens": 11, "completion_tokens": 7},
        tool_calls=[ToolCallRequest(id="call_1", name="exec", arguments={"command": "pwd"})],
        tool_results=["ok"],
        tool_events=[{"name": "exec", "status": "ok", "detail": "ok"}],
        final_content="done",
        stop_reason="completed",
    )


@pytest.mark.asyncio
async def test_external_hook_bridge_serializes_payload_and_env(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    hook = ExternalHookBridge(
        _python_hook(
            "import json, os, pathlib, sys; "
            "data = json.load(sys.stdin); "
            "data['env'] = {"
            "'event': os.environ.get('HAHOBOT_HOOK_EVENT'), "
            "'session_key': os.environ.get('HAHOBOT_HOOK_SESSION_KEY'), "
            "'workspace': os.environ.get('HAHOBOT_HOOK_WORKSPACE'), "
            "'model': os.environ.get('HAHOBOT_HOOK_MODEL')}; "
            "pathlib.Path(os.environ['PAYLOAD_PATH']).write_text("
            "json.dumps(data, ensure_ascii=False), encoding='utf-8')"
        ),
        env={"PAYLOAD_PATH": str(payload_path)},
    )

    await hook.before_execute_tools(_context(tmp_path))

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["event"] == "before_execute_tools"
    assert payload["context"]["iteration"] == 1
    assert payload["context"]["workspace"] == str(tmp_path)
    assert payload["context"]["session_key"] == "cli:test"
    assert payload["context"]["model"] == "openai/gpt-4.1"
    assert payload["context"]["tool_calls"] == [
        {
            "id": "call_1",
            "name": "exec",
            "arguments": {"command": "pwd"},
            "extra_content": None,
            "provider_specific_fields": None,
            "function_provider_specific_fields": None,
        }
    ]
    assert payload["env"] == {
        "event": "before_execute_tools",
        "session_key": "cli:test",
        "workspace": str(tmp_path),
        "model": "openai/gpt-4.1",
    }


@pytest.mark.asyncio
async def test_external_hook_bridge_blocks_on_continue_false(tmp_path: Path) -> None:
    hook = ExternalHookBridge(
        _python_hook(
            "import json, sys; "
            "json.load(sys.stdin); "
            "print(json.dumps({'continue': False, 'message': 'blocked by test'}))"
        )
    )

    with pytest.raises(ExternalHookBridgeBlocked, match="blocked by test"):
        await hook.before_iteration(_context(tmp_path))


@pytest.mark.asyncio
async def test_external_hook_bridge_fail_open_ignores_nonzero_exit(tmp_path: Path) -> None:
    hook = ExternalHookBridge(
        _python_hook("import json, sys; json.load(sys.stdin); sys.stderr.write('boom'); raise SystemExit(1)")
    )

    await hook.before_iteration(_context(tmp_path))


@pytest.mark.asyncio
async def test_external_hook_bridge_fail_closed_raises_nonzero_exit(tmp_path: Path) -> None:
    hook = ExternalHookBridge(
        _python_hook("import json, sys; json.load(sys.stdin); sys.stderr.write('boom'); raise SystemExit(1)"),
        fail_open=False,
    )

    with pytest.raises(ExternalHookBridgeError, match="boom"):
        await hook.before_iteration(_context(tmp_path))


def test_external_hook_bridge_stream_events_are_opt_in() -> None:
    hook = ExternalHookBridge(_python_hook("import json, sys; json.load(sys.stdin)"))
    stream_hook = ExternalHookBridge(
        _python_hook("import json, sys; json.load(sys.stdin)"),
        events=["before_iteration", "on_stream"],
    )

    assert hook.wants_streaming() is False
    assert stream_hook.wants_streaming() is True


@pytest.mark.asyncio
async def test_external_hook_bridge_block_message_reaches_bus_clients(tmp_path: Path) -> None:
    from hahobot.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096

    hook = ExternalHookBridge(
        _python_hook(
            "import json, sys; "
            "json.load(sys.stdin); "
            "print(json.dumps({'continue': False, 'message': 'blocked by test'}))"
        )
    )

    with patch("hahobot.agent.loop.ContextBuilder"), \
         patch("hahobot.agent.loop.SessionManager"), \
         patch("hahobot.agent.loop.SubagentManager") as mock_sub_mgr, \
         patch("hahobot.agent.loop.Consolidator") as mock_consolidator, \
         patch("hahobot.agent.loop.Dream"):
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        mock_consolidator.return_value.maybe_consolidate_by_tokens = AsyncMock(return_value=None)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, hooks=[hook])

    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))
    loop.tools.get_definitions = MagicMock(return_value=[])

    run_task = asyncio.create_task(loop.run())
    try:
        await bus.publish_inbound(InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="hi",
        ))
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=2)
        assert outbound.content == "blocked by test"
    finally:
        loop.stop()
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
