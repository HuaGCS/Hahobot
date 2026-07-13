"""Tests for subprocess cleanup in ExecTool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hahobot.agent.tools.shell import ExecTool


@pytest.mark.asyncio
async def test_kill_process_skips_kill_when_process_already_exited() -> None:
    process = AsyncMock()
    process.pid = 4242
    process.returncode = 0
    process.kill = MagicMock(side_effect=ProcessLookupError("already dead"))

    with patch("hahobot.agent.tools.shell._reap_pid") as reap_pid:
        await ExecTool._kill_process(process)

    process.kill.assert_not_called()
    process.wait.assert_not_called()
    reap_pid.assert_called_once_with(4242)


@pytest.mark.asyncio
async def test_kill_process_reaps_even_if_kill_races_with_exit() -> None:
    process = AsyncMock()
    process.pid = 4343
    process.returncode = None
    process.kill = MagicMock(side_effect=ProcessLookupError("already dead"))
    process.wait = AsyncMock(return_value=0)

    with patch("hahobot.agent.tools.shell._reap_pid") as reap_pid:
        await ExecTool._kill_process(process)

    process.kill.assert_called_once()
    reap_pid.assert_called_once_with(4343)
