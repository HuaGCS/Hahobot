"""Hardening against degenerate tool calls (name=None/"").

Ported from nanobot 8248d075: a nameless tool_use block cannot be executed and,
if persisted and replayed, makes the Anthropic API reject the whole request
("tool_use.name: Input should be a valid string"), permanently wedging the
session. The runner drops such calls before persistence; tool-hint formatting
and name validation must not raise on them.
"""

from __future__ import annotations

from hahobot.providers.base import ToolCallRequest
from hahobot.utils.tool_hints import format_tool_hints


def test_has_valid_name():
    assert ToolCallRequest(id="1", name="read_file", arguments={}).has_valid_name()
    assert not ToolCallRequest(id="1", name="", arguments={}).has_valid_name()
    # name is typed str but can be None at runtime from a bad relay.
    assert not ToolCallRequest(id="1", name=None, arguments={}).has_valid_name()  # type: ignore[arg-type]


def test_format_tool_hints_skips_nameless_call():
    calls = [
        ToolCallRequest(id="1", name="read_file", arguments={"path": "foo.txt"}),
        ToolCallRequest(id="2", name="", arguments={}),
        ToolCallRequest(id="3", name=None, arguments={}),  # type: ignore[arg-type]
    ]
    # Must not raise AttributeError and must still format the valid call.
    result = format_tool_hints(calls)
    assert "read foo.txt" in result


def test_format_tool_hints_all_nameless_is_empty():
    calls = [ToolCallRequest(id="1", name="", arguments={})]
    assert format_tool_hints(calls) == ""
