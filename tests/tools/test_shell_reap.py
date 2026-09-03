"""Tests for subprocess cleanup in ExecTool."""

from __future__ import annotations

import asyncio
import shlex
import signal
import sys
from types import SimpleNamespace
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


@pytest.mark.asyncio
async def test_kill_process_tree_terminates_posix_process_group() -> None:
    process = AsyncMock()
    process.pid = 4444
    process.returncode = None
    process.kill = MagicMock()
    process.wait = AsyncMock(return_value=0)

    with (
        patch("hahobot.agent.tools.shell._IS_WINDOWS", False),
        patch("hahobot.agent.tools.shell.os.killpg") as killpg,
        patch("hahobot.agent.tools.shell._reap_pid") as reap_pid,
    ):
        await ExecTool._kill_process_tree(process)

    killpg.assert_called_once_with(4444, signal.SIGKILL)
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
    reap_pid.assert_called_once_with(4444)


@pytest.mark.asyncio
async def test_kill_process_tree_uses_windows_job_owner() -> None:
    process = AsyncMock()
    process.pid = 4545
    process.returncode = 0
    owner = SimpleNamespace(
        creation_flags=0,
        assign_and_resume=MagicMock(),
        release=MagicMock(),
        terminate=MagicMock(),
    )
    process._hahobot_process_tree_owner = owner

    with patch("hahobot.agent.tools.shell._reap_pid") as reap_pid:
        await ExecTool._kill_process_tree(process)

    owner.terminate.assert_called_once_with()
    assert not hasattr(process, "_hahobot_process_tree_owner")
    process.kill.assert_not_called()
    process.wait.assert_not_called()
    reap_pid.assert_called_once_with(4545)


def test_release_process_tree_releases_windows_job_owner() -> None:
    process = AsyncMock()
    owner = SimpleNamespace(
        creation_flags=0,
        assign_and_resume=MagicMock(),
        release=MagicMock(),
        terminate=MagicMock(),
    )
    process._hahobot_process_tree_owner = owner

    ExecTool._release_process_tree(process)

    owner.release.assert_called_once_with()
    assert not hasattr(process, "_hahobot_process_tree_owner")


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX process groups")
@pytest.mark.asyncio
async def test_timeout_kills_descendant_after_root_exits(tmp_path) -> None:
    marker = tmp_path / "child-survived-root"
    child_code = (
        f"import pathlib,time; time.sleep(2.5); pathlib.Path({str(marker)!r}).write_text('alive')"
    )
    parent_code = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {child_code!r}])"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(parent_code)}"

    result = await ExecTool()._execute_after_safety(command, cwd=str(tmp_path), timeout=1)

    assert "timed out" in result.lower()
    await asyncio.sleep(2)
    assert not marker.exists()
