"""Tests for MCP subprocess stderr capture via loguru."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack

from loguru import logger

from hahobot.agent.tools.mcp import _stderr_to_logger


def _poll_records(records: list[str], expected_count: int, timeout: float = 2.0) -> None:
    """Poll *records* until it has at least *expected_count* entries or *timeout* expires."""
    import time

    deadline = time.monotonic() + timeout
    while len(records) < expected_count:
        if time.monotonic() > deadline:
            break
        time.sleep(0.05)


class TestStderrToLogger:
    """Tests for _stderr_to_logger pipe-and-thread stderr capture."""

    def test_forwards_lines_to_loguru(self) -> None:
        """Writing a meaningful line produces a DEBUG record with the server tag."""
        stack = AsyncExitStack()
        records: list[str] = []
        sink_id = logger.add(records.append, format="{message}", level="DEBUG")

        try:
            writer = _stderr_to_logger("test_server", stack)
            assert writer is not None, "should return a writable pipe"

            writer.write("hello from stderr\n")
            writer.close()

            _poll_records(records, 1)

            assert len(records) >= 1, f"expected at least 1 record, got {records}"
            record = records[-1]
            assert "[mcp:test_server]" in record, f"expected server tag in {record!r}"
            assert "hello from stderr" in record, f"expected line text in {record!r}"
        finally:
            logger.remove(sink_id)
            asyncio.run(stack.aclose())

    def test_blank_lines_are_filtered(self) -> None:
        """Blank or whitespace-only lines do not produce a log record."""
        stack = AsyncExitStack()
        records: list[str] = []
        sink_id = logger.add(records.append, format="{message}", level="DEBUG")

        try:
            writer = _stderr_to_logger("test_server", stack)
            assert writer is not None

            # Write a blank line and a whitespace-only line
            writer.write("\n")
            writer.write("  \t  \n")
            writer.write("actual content\n")
            writer.close()

            _poll_records(records, 1)

            # Only the "actual content" line should have been logged
            meaningful = [r for r in records if "[mcp:test_server]" in r]
            assert len(meaningful) == 1, f"expected exactly 1 meaningful record, got {meaningful}"
            assert "actual content" in meaningful[0]
        finally:
            logger.remove(sink_id)
            asyncio.run(stack.aclose())

    def test_pipe_failure_returns_none(self, monkeypatch) -> None:
        """If os.pipe() fails, _stderr_to_logger returns None and does not raise."""

        def failing_pipe() -> list[int]:
            raise OSError("pipe failed")

        monkeypatch.setattr(os, "pipe", failing_pipe)

        stack = AsyncExitStack()
        result = _stderr_to_logger("failing_server", stack)
        assert result is None, "should return None on setup failure"
        asyncio.run(stack.aclose())
