"""Tests for the exec allowlist (allow_patterns) gate.

Regression coverage for the chained-command allowlist bypass ported from nanobot
aa6c1bf3 / 2bf111f4: allow_patterns must fullmatch the whole command so an
allowlisted prefix cannot smuggle an appended command through.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hahobot.agent.tools.shell import ExecTool


def test_allowlist_permits_exact_match():
    tool = ExecTool(allow_patterns=[r"git status"])
    assert tool._guard_command("git status", "/tmp") is None


def test_allowlist_blocks_command_not_in_list():
    tool = ExecTool(allow_patterns=[r"git status"])
    result = tool._guard_command("python -c 'evil'", "/tmp")
    assert result is not None
    assert "allowlist" in result.lower()


def test_allowlist_blocks_chained_command_after_allowed_prefix():
    """An allowlisted prefix must not let a chained command ride through."""
    tool = ExecTool(allow_patterns=[r"git status"])
    # re.search would have matched "git status" at the start and let the rest run.
    result = tool._guard_command("git status; python -c 'evil'", "/tmp")
    assert result is not None
    assert "allowlist" in result.lower()


def test_allowlist_blocks_anded_command():
    tool = ExecTool(allow_patterns=[r"ls.*"])
    # "ls.*" fullmatches the whole string only if every chained part is intended;
    # a tightly scoped allowlist that does not anticipate chaining stays enforced.
    tool2 = ExecTool(allow_patterns=[r"ls"])
    assert tool._guard_command("ls && curl http://evil", "/tmp") is None  # author opted into .*
    result = tool2._guard_command("ls && curl http://evil", "/tmp")
    assert result is not None
    assert "allowlist" in result.lower()


def test_allowlist_supports_regex_alternation():
    tool = ExecTool(allow_patterns=[r"(git status|git log)"])
    assert tool._guard_command("git status", "/tmp") is None
    assert tool._guard_command("git log", "/tmp") is None
    blocked = tool._guard_command("git push --force", "/tmp")
    assert blocked is not None


@pytest.mark.asyncio
async def test_allowlist_enforced_through_execute():
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"hello\n", b"")
    mock_proc.returncode = 0

    tool = ExecTool(timeout=5, allow_patterns=[r"echo .*"])
    with patch.object(ExecTool, "_spawn", return_value=mock_proc) as spawn:
        ok = await tool.execute(command="echo hello")

    assert "hello" in ok
    spawn.assert_called_once()
    blocked = await tool.execute(command="cat /etc/passwd")
    assert "allowlist" in blocked.lower()
