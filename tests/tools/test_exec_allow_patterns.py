"""Tests for the exec allowlist (allow_patterns) gate.

Regression coverage for chained-command allowlist handling ported from nanobot:
each top-level shell segment must independently match an allow pattern.
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
    result = tool._guard_command("ls && curl http://evil", "/tmp")
    assert result is not None
    assert "allowlist" in result.lower()


def test_allowlist_permits_each_approved_chained_segment():
    tool = ExecTool(allow_patterns=[r"git status", r"git log(?: .*)?"])
    assert tool._guard_command("git status && git log -1", "/tmp") is None


def test_allowlist_does_not_split_quoted_or_parenthesized_operators():
    tool = ExecTool(allow_patterns=[r"printf 'a;b'", r"\(printf x; printf y\)"])
    assert tool._guard_command("printf 'a;b'", "/tmp") is None
    assert tool._guard_command("(printf x; printf y)", "/tmp") is None


def test_allowlist_preserves_background_operator_for_matching():
    tool = ExecTool(allow_patterns=[r"sleep 1 &", r"echo done"])
    assert tool._guard_command("sleep 1 & echo done", "/tmp") is None

    foreground_only = ExecTool(allow_patterns=[r"sleep 1", r"echo done"])
    assert foreground_only._guard_command("sleep 1 & echo done", "/tmp") is not None


def test_allowlist_does_not_override_deny_patterns():
    tool = ExecTool(allow_patterns=[r"rm -rf build"])
    result = tool._guard_command("rm -rf build", "/tmp")
    assert result is not None
    assert "dangerous" in result.lower()


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
