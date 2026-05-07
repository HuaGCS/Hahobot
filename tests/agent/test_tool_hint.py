"""Tests for tool hint formatting (hahobot.utils.tool_hints)."""

from hahobot.providers.base import ToolCallRequest
from hahobot.utils.tool_hints import format_tool_hints


def _tc(name: str, args) -> ToolCallRequest:
    return ToolCallRequest(id="c1", name=name, arguments=args)


def _hint(calls, max_length=40):
    """Shortcut for format_tool_hints."""
    return format_tool_hints(calls, max_length=max_length)


class TestToolHintKnownTools:
    """Test registered tool types produce correct formatted output."""

    def test_read_file_short_path(self):
        result = _hint([_tc("read_file", {"path": "foo.txt"})])
        assert result == 'read foo.txt'

    def test_read_file_long_path(self):
        result = _hint([_tc("read_file", {"path": "/home/user/.local/share/uv/tools/hahobot/agent/loop.py"})])
        assert "loop.py" in result
        assert "read " in result

    def test_write_file_shows_path_not_content(self):
        result = _hint([_tc("write_file", {"path": "docs/api.md", "content": "# API Reference\n\nLong content..."})])
        assert result == "write docs/api.md"

    def test_edit_shows_path(self):
        result = _hint([_tc("edit", {"file_path": "src/main.py", "old_string": "x", "new_string": "y"})])
        assert "main.py" in result
        assert "edit " in result

    def test_glob_shows_pattern(self):
        result = _hint([_tc("glob", {"pattern": "**/*.py", "path": "src"})])
        assert result == 'glob "**/*.py"'

    def test_grep_shows_pattern(self):
        result = _hint([_tc("grep", {"pattern": "TODO|FIXME", "path": "src"})])
        assert result == 'grep "TODO|FIXME"'

    def test_exec_shows_command(self):
        result = _hint([_tc("exec", {"command": "npm install typescript"})])
        assert result == "$ npm install typescript"

    def test_exec_truncates_long_command(self):
        cmd = "cd /very/long/path && cat file && echo done && sleep 1 && ls -la"
        result = _hint([_tc("exec", {"command": cmd})])
        assert result.startswith("$ ")
        assert len(result) <= 50  # reasonable limit

    def test_exec_abbreviates_quoted_paths(self):
        cmd = 'cat "/Users/demo/Documents/very long folder/notes.txt"'
        result = _hint([_tc("exec", {"command": cmd})])
        assert result.startswith("$ cat ")
        assert "notes.txt" in result
        assert "/Users/demo/Documents/very long folder/notes.txt" not in result

    def test_web_search(self):
        result = _hint([_tc("web_search", {"query": "Claude 4 vs GPT-4"})])
        assert result == 'search "Claude 4 vs GPT-4"'

    def test_web_fetch(self):
        result = _hint([_tc("web_fetch", {"url": "https://example.com/page"})])
        assert result == "fetch https://example.com/page"


class TestToolHintMCP:
    """Test MCP tools are abbreviated to server::tool format."""

    def test_mcp_standard_format(self):
        result = _hint([_tc("mcp_4_5v_mcp__analyze_image", {"imageSource": "https://img.jpg", "prompt": "describe"})])
        assert "4_5v" in result
        assert "analyze_image" in result

    def test_mcp_simple_name(self):
        result = _hint([_tc("mcp_github__create_issue", {"title": "Bug fix"})])
        assert "github" in result
        assert "create_issue" in result


class TestToolHintFallback:
    """Test unknown tools fall back to original behavior."""

    def test_unknown_tool_with_string_arg(self):
        result = _hint([_tc("custom_tool", {"data": "hello world"})])
        assert result == 'custom_tool("hello world")'

    def test_unknown_tool_with_long_arg_truncates(self):
        long_val = "a" * 60
        result = _hint([_tc("custom_tool", {"data": long_val})])
        assert len(result) < 80
        assert "\u2026" in result

    def test_unknown_tool_no_string_arg(self):
        result = _hint([_tc("custom_tool", {"count": 42})])
        assert result == "custom_tool"

    def test_empty_tool_calls(self):
        result = _hint([])
        assert result == ""


class TestToolHintFolding:
    """Test consecutive identical formatted hints are folded."""

    def test_single_call_no_fold(self):
        calls = [_tc("grep", {"pattern": "*.py"})]
        result = _hint(calls)
        assert "\u00d7" not in result

    def test_two_consecutive_same_hint_folded(self):
        calls = [
            _tc("grep", {"pattern": "*.py"}),
            _tc("grep", {"pattern": "*.py"}),
        ]
        result = _hint(calls)
        assert "\u00d7 2" in result

    def test_three_consecutive_same_hint_folded(self):
        calls = [
            _tc("read_file", {"path": "a.py"}),
            _tc("read_file", {"path": "a.py"}),
            _tc("read_file", {"path": "a.py"}),
        ]
        result = _hint(calls)
        assert "\u00d7 3" in result

    def test_different_tools_not_folded(self):
        calls = [
            _tc("grep", {"pattern": "TODO"}),
            _tc("read_file", {"path": "a.py"}),
        ]
        result = _hint(calls)
        assert "\u00d7" not in result

    def test_interleaved_same_tools_not_folded(self):
        calls = [
            _tc("grep", {"pattern": "a"}),
            _tc("read_file", {"path": "f.py"}),
            _tc("grep", {"pattern": "b"}),
        ]
        result = _hint(calls)
        assert "\u00d7" not in result

    def test_same_tool_different_formatted_hints_not_folded(self):
        calls = [
            _tc("read_file", {"path": "a.py"}),
            _tc("read_file", {"path": "b.py"}),
        ]
        result = _hint(calls)
        assert "\u00d7" not in result


class TestToolHintMultipleCalls:
    """Test multiple different tool calls are comma-separated."""

    def test_two_different_tools(self):
        calls = [
            _tc("grep", {"pattern": "TODO"}),
            _tc("read_file", {"path": "main.py"}),
        ]
        result = _hint(calls)
        assert 'grep "TODO"' in result
        assert "read main.py" in result
        assert ", " in result


class TestToolHintEdgeCases:
    """Test edge cases and defensive handling (G1, G2)."""

    def test_known_tool_empty_list_args(self):
        """C1/G1: Empty list arguments should not crash."""
        result = _hint([_tc("read_file", [])])
        assert result == "read_file"

    def test_known_tool_none_args(self):
        """G2: None arguments should not crash."""
        result = _hint([_tc("read_file", None)])
        assert result == "read_file"

    def test_fallback_empty_list_args(self):
        """C1: Empty list args in fallback should not crash."""
        result = _hint([_tc("custom_tool", [])])
        assert result == "custom_tool"

    def test_fallback_none_args(self):
        """G2: None args in fallback should not crash."""
        result = _hint([_tc("custom_tool", None)])
        assert result == "custom_tool"

    def test_list_dir_registered(self):
        """S2: list_dir should use 'ls' format."""
        result = _hint([_tc("list_dir", {"path": "/tmp"})])
        assert result == "ls /tmp"


class TestToolHintMixedFolding:
    """G4: Mixed folding groups depend on identical formatted hints."""

    def test_read_read_grep_grep_read(self):
        """read×2, grep×2, read — should produce two separate groups."""
        calls = [
            _tc("read_file", {"path": "a.py"}),
            _tc("read_file", {"path": "a.py"}),
            _tc("grep", {"pattern": "x"}),
            _tc("grep", {"pattern": "x"}),
            _tc("read_file", {"path": "c.py"}),
        ]
        result = _hint(calls)
        assert "\u00d7 2" in result
        # Should have 3 groups: read×2, grep×2, read
        parts = result.split(", ")
        assert len(parts) == 3


class TestToolHintMaxLength:
    """Test max_length parameter controls truncation of tool hints."""

    def test_exec_default_truncates_at_40(self):
        cmd = "cd /very/long/path/to/some/project && npm run build && npm test"
        result = _hint([_tc("exec", {"command": cmd})], max_length=40)
        assert result.startswith("$ ")
        assert "\u2026" in result

    def test_exec_larger_max_length_shows_more(self):
        cmd = "cd /very/long/path/to/some/project && npm run build && npm test"
        short = _hint([_tc("exec", {"command": cmd})], max_length=40)
        long = _hint([_tc("exec", {"command": cmd})], max_length=120)
        assert len(long) > len(short)
        assert "npm test" in long

    def test_path_tools_respect_max_length(self):
        path = "/home/user/projects/hahobot/hahobot/agent/loop.py"
        short = _hint([_tc("read_file", {"path": path})], max_length=20)
        long = _hint([_tc("read_file", {"path": path})], max_length=80)
        assert len(long) > len(short)
        assert "loop.py" in short

    def test_fallback_respects_max_length(self):
        long_val = "a" * 100
        result = _hint([_tc("custom_tool", {"data": long_val})], max_length=60)
        result_40 = _hint([_tc("custom_tool", {"data": long_val})], max_length=40)
        assert "\u2026" in result
        assert len(result) > len(result_40)

    def test_mcp_respects_max_length(self):
        long_url = "https://example.com/very/long/path/to/resource"
        result = _hint([_tc("mcp_github__fetch", {"url": long_url})], max_length=80)
        result_40 = _hint([_tc("mcp_github__fetch", {"url": long_url})], max_length=40)
        assert len(result) >= len(result_40)
