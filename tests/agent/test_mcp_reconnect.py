"""Tests for MCP session-termination detection and auto-reconnect."""

from __future__ import annotations

from typing import Any

from hahobot.agent.tools.mcp import (
    MCPToolWrapper,
    _is_session_terminated,
    _MCPServerConnection,
    _MCPWrapperBase,
)

# ── Fake objects for testing ───────────────────────────────────────────────────


class _FakeCoordinator:
    """Simulates a coordinator that swaps the wrapper session on reconnect."""

    def __init__(self, new_session: Any) -> None:
        self._new_session = new_session
        self._generation = 1
        self.reconnect_count = 0

    async def reconnect(self, wrapper: _MCPWrapperBase) -> bool:
        self.reconnect_count += 1
        wrapper._session = self._new_session
        wrapper._generation = self._generation
        return True


class _FakeFailingCoordinator:
    """Simulates a coordinator whose rebuild fails."""

    def __init__(self) -> None:
        self._generation = 0
        self.reconnect_count = 0

    async def reconnect(self, wrapper: _MCPWrapperBase) -> bool:
        self.reconnect_count += 1
        return False


class _FakeToolResult:
    def __init__(self, text: str) -> None:
        from mcp.types import TextContent

        self.content = [TextContent(type="text", text=text)]


class _FakeWorkingSession:
    """Always succeeds."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> _FakeToolResult:
        return _FakeToolResult("hello from working session")


class _FakeTerminatingSession:
    """Fails the first call with 'Session terminated', then succeeds."""

    def __init__(self) -> None:
        self.call_count = 0

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> _FakeToolResult:
        self.call_count += 1
        if self.call_count == 1:
            raise RuntimeError("Session terminated")
        return _FakeToolResult("hello after reconnect")


class _FakeBoomSession:
    """Always raises a non-termination error."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> _FakeToolResult:
        raise ValueError("boom")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_tool_def(name: str = "test_tool") -> Any:
    """Create a minimal tool-definition-like object."""
    return type(
        "_ToolDef",
        (),
        {
            "name": name,
            "description": f"A test tool named {name}",
            "inputSchema": {"type": "object", "properties": {}},
        },
    )()


# ── _is_session_terminated ─────────────────────────────────────────────────────


class TestIsSessionTerminated:
    def test_session_terminated_message(self) -> None:
        """Detect 'session terminated' in the exception string."""
        assert _is_session_terminated(RuntimeError("Session terminated"))

    def test_connection_closed_message(self) -> None:
        """Detect 'connection closed' in the exception string."""
        assert _is_session_terminated(RuntimeError("connection closed"))

    def test_case_insensitive(self) -> None:
        """Detection is case-insensitive."""
        assert _is_session_terminated(RuntimeError("SESSION TERMINATED"))
        assert _is_session_terminated(RuntimeError("Connection Closed"))

    def test_substring_in_message(self) -> None:
        """Detection works when marker is a substring of a longer message."""
        assert _is_session_terminated(RuntimeError("The MCP session terminated unexpectedly"))
        assert _is_session_terminated(RuntimeError("connection closed by remote host"))

    def test_error_attribute(self) -> None:
        """Detection reads exc.error.message when available."""
        fake_error = type("_Error", (), {"message": "Session terminated"})()
        exc = type("_Exc", (), {"error": fake_error})()
        assert _is_session_terminated(exc)

    def test_unrelated_error(self) -> None:
        """Non-termination errors return False."""
        assert not _is_session_terminated(ValueError("something else"))
        assert not _is_session_terminated(RuntimeError("timeout"))

    def test_error_attribute_no_match(self) -> None:
        """exc.error.message without termination markers returns False."""
        fake_error = type("_Error", (), {"message": "invalid params"})()
        exc = type("_Exc", (), {"error": fake_error})()
        assert not _is_session_terminated(exc)


# ── execute() reconnect flow ──────────────────────────────────────────────────


class TestExecuteReconnect:
    """Test the full execute() retry loop via MCPToolWrapper."""

    async def test_reconnects_on_session_terminated(self) -> None:
        """A Session-terminated error triggers exactly one reconnect then succeeds."""
        wrapper = MCPToolWrapper(
            _FakeTerminatingSession(),
            "test_server",
            _make_tool_def(),
        )
        coordinator = _FakeCoordinator(_FakeWorkingSession())
        wrapper._coordinator = coordinator

        result = await wrapper.execute()

        assert result == "hello from working session"
        assert coordinator.reconnect_count == 1

    async def test_no_reconnect_on_non_termination_error(self) -> None:
        """A plain error does not trigger reconnect."""
        wrapper = MCPToolWrapper(
            _FakeBoomSession(),
            "test_server",
            _make_tool_def(),
        )
        coordinator = _FakeCoordinator(_FakeWorkingSession())
        wrapper._coordinator = coordinator

        result = await wrapper.execute()

        assert "ValueError" in result
        assert coordinator.reconnect_count == 0

    async def test_only_one_retry_attempt(self) -> None:
        """If reconnect succeeds but the new session also fails, the wrapper
        falls through to the error path immediately (only one retry)."""
        wrapper = MCPToolWrapper(
            _FakeTerminatingSession(),
            "test_server",
            _make_tool_def(),
        )
        # After reconnect the wrapper gets a _FakeBoomSession that raises:
        coordinator = _FakeCoordinator(_FakeBoomSession())
        wrapper._coordinator = coordinator

        result = await wrapper.execute()

        # Second call falls through to error path
        assert "ValueError" in result
        # Reconnect was attempted
        assert coordinator.reconnect_count == 1

    async def test_reconnect_failure_falls_through(self) -> None:
        """When the coordinator fails to rebuild, the wrapper returns the
        original error string without retrying."""
        wrapper = MCPToolWrapper(
            _FakeTerminatingSession(),
            "test_server",
            _make_tool_def(),
        )
        wrapper._coordinator = _FakeFailingCoordinator()

        result = await wrapper.execute()

        assert "RuntimeError" in result
        assert "Session terminated" not in result


# ── Coordinator generation guard ──────────────────────────────────────────────


class TestCoordinatorGenerationGuard:
    """_MCPServerConnection generation-based concurrency safety."""

    async def test_rebuild_once_for_two_stale_wrappers(self) -> None:
        """Two wrappers with stale generations trigger exactly one rebuild."""
        from contextlib import AsyncExitStack

        stack = AsyncExitStack()
        session_1 = _FakeWorkingSession()

        class _FakeCfg:
            type = "stdio"
            command = "/bin/false"
            args: list[str] = []
            env: dict[str, str] | None = None
            headers: dict[str, str] | None = None
            url: str | None = None
            tool_timeout = 30
            enabled_tools = ["*"]

        rebuild_count = 0

        async def fake_open_session(name, cfg, stack) -> Any:
            nonlocal rebuild_count
            rebuild_count += 1
            return _FakeWorkingSession()

        w1 = MCPToolWrapper(session_1, "srv", _make_tool_def("tool_a"))
        w2 = MCPToolWrapper(session_1, "srv", _make_tool_def("tool_b"))

        coordinator = _MCPServerConnection("srv", _FakeCfg(), stack, session_1, [w1, w2])
        # Monkey-patch _open_session so the real transport code never runs
        import hahobot.agent.tools.mcp as mcp_mod

        original_open = mcp_mod._open_session
        mcp_mod._open_session = fake_open_session
        try:
            # First caller — should rebuild
            ok1 = await coordinator.reconnect(w1)
            assert ok1, "first reconnect should succeed"
            assert rebuild_count == 1, "first reconnect triggers one rebuild"
            # The broadcast updates only _session, not _generation
            assert w1._generation == 0

            # Second caller — stale generation, should adopt without rebuilding
            ok2 = await coordinator.reconnect(w2)
            assert ok2, "second reconnect should succeed (adopt)"
            assert rebuild_count == 1, "second reconnect does NOT trigger another rebuild"
            # On adoption the wrapper receives the coordinator's current generation
            assert w2._generation == 1
        finally:
            mcp_mod._open_session = original_open
