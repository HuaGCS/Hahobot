"""Regression: MCP-derived tool names must stay within Anthropic's 64-char limit.

An over-length `mcp_<server>_<tool>` name 400s the Anthropic Messages API and
bricks the session; the wrapper still dispatches via `_original_name`, so capping
the model-facing name is safe. Ported from nanobot `3f9fb63d` (length core).
"""

from hahobot.agent.tools.mcp import (
    _MAX_TOOL_NAME_LENGTH,
    MCPToolWrapper,
    _limit_tool_name,
)


class _ToolDef:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "desc"
        self.inputSchema = {"type": "object", "properties": {}}


def test_short_name_unchanged() -> None:
    assert _limit_tool_name("mcp_srv_do_thing") == "mcp_srv_do_thing"


def test_long_name_capped_to_limit() -> None:
    long = "mcp_server_" + "x" * 200
    capped = _limit_tool_name(long)
    assert len(capped) == _MAX_TOOL_NAME_LENGTH
    assert capped.startswith("mcp_server_")


def test_distinct_long_names_do_not_collide() -> None:
    a = _limit_tool_name("mcp_server_" + "a" * 200)
    b = _limit_tool_name("mcp_server_" + "b" * 200)
    # Same truncated prefix, but the sha1 suffix keeps them distinct.
    assert a != b


def test_wrapper_caps_name_but_keeps_original_for_dispatch() -> None:
    tool = _ToolDef("t" * 100)
    wrapper = MCPToolWrapper(session=None, server_name="s" * 40, tool_def=tool)
    assert len(wrapper.name) <= _MAX_TOOL_NAME_LENGTH
    # The server is still called with the untruncated original name.
    assert wrapper._original_name == "t" * 100
