"""Tests for MCP runtime connection cleanup semantics."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hahobot.agent.mcp_runtime import MCPRuntime


def _runtime(tmp_path: Path) -> MCPRuntime:
    tools = MagicMock()
    tools.tool_names = []
    return MCPRuntime(
        tools=tools,
        workspace=tmp_path,
        truncate_prompt_text=lambda text, limit: text[:limit],
    )


class _LeakyCancelledStack:
    async def aclose(self) -> None:
        raise asyncio.CancelledError


class _BlockingStack:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def aclose(self) -> None:
        self.started.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_reset_connections_ignores_sdk_leaked_cancelled_error(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.stack = _LeakyCancelledStack()  # type: ignore[assignment]
    runtime.connected = True

    await runtime.reset_connections()

    assert runtime.stack is None
    assert runtime.connected is False


@pytest.mark.asyncio
async def test_reset_connections_preserves_external_cancellation(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stack = _BlockingStack()
    runtime.stack = stack  # type: ignore[assignment]

    task = asyncio.create_task(runtime.reset_connections())
    await stack.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_connect_preserves_external_cancellation(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.servers = {"demo": {"command": "demo"}}
    started = asyncio.Event()

    async def _blocking_connect(*_args, **_kwargs) -> None:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("hahobot.agent.tools.mcp.connect_mcp_servers", _blocking_connect)

    task = asyncio.create_task(runtime.connect())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.stack is None
    assert runtime.connected is False
    assert runtime.connecting is False
