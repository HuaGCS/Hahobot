"""Bounded subprocess-output capture regressions for the exec tool."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, patch

import pytest

from hahobot.agent.tools.shell import (
    ExecTool,
    _BoundedTextCapture,
    _read_stream_bounded,
)


class _StreamingProcess:
    def __init__(self, *, returncode: int = 7, chunks: int = 128) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode: int | None = None
        self.pid = 12345
        self._final_returncode = returncode
        self._chunks = chunks
        self.communicate_called = False
        self.wait_started = asyncio.Event()

    async def communicate(self):
        self.communicate_called = True
        raise AssertionError("real pipe readers must not use communicate()")

    async def wait(self) -> int:
        self.wait_started.set()
        self.stdout.feed_data(b"OUT-START\n")
        self.stderr.feed_data(b"ERR-START\n")
        for _ in range(self._chunks):
            self.stdout.feed_data(b"x" * 4096)
            self.stderr.feed_data(b"y" * 4096)
            await asyncio.sleep(0)
        self.stdout.feed_data(b"\nOUT-END")
        self.stderr.feed_data(b"\nERR-END")
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.returncode = self._final_returncode
        return self._final_returncode


def test_bounded_capture_retains_only_head_and_tail() -> None:
    capture = _BoundedTextCapture(100)

    capture.feed("START" + "x" * 10_000 + "END")

    assert capture.total_chars == 10_008
    assert len(capture.preview) == 100
    assert capture.preview.startswith("START")
    assert capture.preview.endswith("END")


@pytest.mark.asyncio
async def test_bounded_reader_preserves_utf8_split_across_chunks() -> None:
    reader = asyncio.StreamReader()
    encoded = "开头🙂结尾".encode()
    task = asyncio.create_task(_read_stream_bounded(reader, max_chars=100))

    for byte in encoded:
        reader.feed_data(bytes([byte]))
        await asyncio.sleep(0)
    reader.feed_eof()

    capture = await task
    assert capture.preview == "开头🙂结尾"
    assert capture.total_chars == len("开头🙂结尾")


@pytest.mark.asyncio
async def test_exec_drains_large_stdout_and_stderr_without_communicate() -> None:
    tool = ExecTool()
    process = _StreamingProcess()

    stdout, stderr = await tool._communicate_bounded(process)  # type: ignore[arg-type]
    result = tool._format_captured_output(stdout, stderr, process.returncode)

    assert process.communicate_called is False
    assert len(stdout.preview) == tool._MAX_OUTPUT
    assert len(stderr.preview) == tool._MAX_OUTPUT
    assert stdout.preview.startswith("OUT-START")
    assert stdout.preview.endswith("OUT-END")
    assert stderr.preview.startswith("ERR-START")
    assert stderr.preview.endswith("ERR-END")
    assert "OUT-START" in result
    assert "ERR-END" in result
    assert "chars truncated" in result
    assert "Exit code: 7" in result


@pytest.mark.asyncio
async def test_exec_bounded_capture_handles_real_noisy_subprocess(tmp_path) -> None:
    tool = ExecTool()
    script = tmp_path / "noisy.py"
    script.write_text(
        """import sys

for _ in range(256):
    sys.stdout.write("x" * 4096)
    sys.stdout.flush()
    sys.stderr.write("y" * 4096)
    sys.stderr.flush()
sys.stdout.write("\\nOUT-END")
sys.stderr.write("\\nERR-END")
""",
        encoding="utf-8",
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await asyncio.wait_for(tool._communicate_bounded(process), timeout=10)

    assert process.returncode == 0
    assert stdout.total_chars > 1_000_000
    assert stderr.total_chars > 1_000_000
    assert len(stdout.preview) == tool._MAX_OUTPUT
    assert len(stderr.preview) == tool._MAX_OUTPUT
    assert stdout.preview.endswith("OUT-END")
    assert stderr.preview.endswith("ERR-END")


@pytest.mark.asyncio
async def test_exec_timeout_cancels_bounded_readers_and_kills_process() -> None:
    tool = ExecTool(timeout=1)
    process = _StreamingProcess(chunks=0)

    async def wait_forever() -> int:
        process.wait_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    process.wait = wait_forever  # type: ignore[method-assign]
    with (
        patch.object(tool, "_spawn", return_value=process),
        patch.object(tool, "_kill_process", new_callable=AsyncMock) as kill,
    ):
        result = await tool._execute_after_safety("echo test", cwd="/tmp", timeout=1)

    assert result == "Error: Command timed out after 1 seconds"
    kill.assert_awaited_once_with(process)


@pytest.mark.asyncio
async def test_exec_cancellation_cancels_bounded_readers_and_kills_process() -> None:
    tool = ExecTool(timeout=60)
    process = _StreamingProcess(chunks=0)

    async def wait_forever() -> int:
        process.wait_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    process.wait = wait_forever  # type: ignore[method-assign]
    with (
        patch.object(tool, "_spawn", return_value=process),
        patch.object(tool, "_kill_process", new_callable=AsyncMock) as kill,
    ):
        task = asyncio.create_task(tool._execute_after_safety("echo test", cwd="/tmp", timeout=60))
        await process.wait_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    kill.assert_awaited_once_with(process)
