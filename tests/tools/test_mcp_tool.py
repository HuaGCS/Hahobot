from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from types import ModuleType, SimpleNamespace

import pytest

from hahobot.agent.tools.mcp import MCPToolWrapper, connect_mcp_servers
from hahobot.agent.tools.registry import ToolRegistry
from hahobot.config.schema import MCPServerConfig


class _FakeTextContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _TaskAffineContext:
    """Async context that records and enforces same-task teardown."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()
        self.enter_task: asyncio.Task | None = None
        self.exit_task: asyncio.Task | None = None
        self.exit_count = 0

    async def __aenter__(self) -> _TaskAffineContext:
        self.enter_task = asyncio.current_task()
        self.entered.set()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.exit_task = asyncio.current_task()
        self.exit_count += 1
        self.exited.set()
        if self.exit_task is not self.enter_task:
            raise RuntimeError("context exited from a different task")
        return False


@pytest.fixture
def fake_mcp_runtime() -> dict[str, object | None]:
    return {"session": None}


@pytest.fixture(autouse=True)
def _fake_mcp_module(
    monkeypatch: pytest.MonkeyPatch,
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    mod = ModuleType("mcp")
    mod.types = SimpleNamespace(TextContent=_FakeTextContent)

    class _FakeStdioServerParameters:
        def __init__(self, command: str, args: list[str], env: dict | None = None) -> None:
            self.command = command
            self.args = args
            self.env = env

    class _FakeClientSession:
        def __init__(self, _read: object, _write: object) -> None:
            self._session = fake_mcp_runtime["session"]

        async def __aenter__(self) -> object:
            return self._session

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    @asynccontextmanager
    async def _fake_stdio_client(_params: object, errlog: object = None):
        yield object(), object()

    @asynccontextmanager
    async def _fake_sse_client(_url: str, httpx_client_factory=None):
        yield object(), object()

    @asynccontextmanager
    async def _fake_streamable_http_client(_url: str, http_client=None):
        yield object(), object(), object()

    mod.ClientSession = _FakeClientSession
    mod.StdioServerParameters = _FakeStdioServerParameters
    monkeypatch.setitem(sys.modules, "mcp", mod)

    client_mod = ModuleType("mcp.client")
    stdio_mod = ModuleType("mcp.client.stdio")
    stdio_mod.stdio_client = _fake_stdio_client
    sse_mod = ModuleType("mcp.client.sse")
    sse_mod.sse_client = _fake_sse_client
    streamable_http_mod = ModuleType("mcp.client.streamable_http")
    streamable_http_mod.streamable_http_client = _fake_streamable_http_client

    monkeypatch.setitem(sys.modules, "mcp.client", client_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", streamable_http_mod)


def _make_wrapper(session: object, *, timeout: float = 0.1) -> MCPToolWrapper:
    tool_def = SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={"type": "object", "properties": {}},
    )
    return MCPToolWrapper(session, "test", tool_def, tool_timeout=timeout)


def test_wrapper_preserves_non_nullable_unions() -> None:
    tool_def = SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [{"type": "string"}, {"type": "integer"}],
                }
            },
        },
    )

    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=None), "test", tool_def)

    assert wrapper.parameters["properties"]["value"]["anyOf"] == [
        {"type": "string"},
        {"type": "integer"},
    ]


def test_wrapper_normalizes_nullable_property_type_union() -> None:
    tool_def = SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
            },
        },
    )

    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=None), "test", tool_def)

    assert wrapper.parameters["properties"]["name"] == {"type": "string", "nullable": True}


def test_wrapper_normalizes_nullable_property_anyof() -> None:
    tool_def = SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "optional name",
                },
            },
        },
    )

    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=None), "test", tool_def)

    assert wrapper.parameters["properties"]["name"] == {
        "type": "string",
        "description": "optional name",
        "nullable": True,
    }


@pytest.mark.asyncio
async def test_execute_returns_text_blocks() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        assert arguments == {"value": 1}
        return SimpleNamespace(content=[_FakeTextContent("hello"), 42])

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute(value=1)

    assert result == "hello\n42"


@pytest.mark.asyncio
async def test_execute_returns_timeout_message() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        await asyncio.sleep(1)
        return SimpleNamespace(content=[])

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool), timeout=0.01)

    result = await wrapper.execute()

    assert result == "(MCP tool call timed out after 0.01s)"


@pytest.mark.asyncio
async def test_execute_handles_server_cancelled_error() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        raise asyncio.CancelledError()

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute()

    assert result == "(MCP tool call was cancelled)"


@pytest.mark.asyncio
async def test_execute_re_raises_external_cancellation() -> None:
    started = asyncio.Event()

    async def call_tool(_name: str, arguments: dict) -> object:
        started.set()
        await asyncio.sleep(60)
        return SimpleNamespace(content=[])

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool), timeout=10)
    task = asyncio.create_task(wrapper.execute())
    await asyncio.wait_for(started.wait(), timeout=1.0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_execute_handles_generic_exception() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        raise RuntimeError("boom")

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute()

    assert result == "(MCP tool call failed: RuntimeError)"


def _make_tool_def(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"{name} tool",
        inputSchema={"type": "object", "properties": {}},
    )


def _make_fake_session(tool_names: list[str]) -> SimpleNamespace:
    async def initialize() -> None:
        return None

    async def list_tools() -> SimpleNamespace:
        return SimpleNamespace(tools=[_make_tool_def(name) for name in tool_names])

    return SimpleNamespace(initialize=initialize, list_tools=list_tools)


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_supports_raw_names(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    fake_mcp_runtime["session"] = _make_fake_session(["demo", "other"])
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {"test": MCPServerConfig(command="fake", enabled_tools=["demo"])},
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == ["mcp_test_demo"]


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_supports_wrapped_names(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    fake_mcp_runtime["session"] = _make_fake_session(["demo", "other"])
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {"test": MCPServerConfig(command="fake", enabled_tools=["mcp_test_demo"])},
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == ["mcp_test_demo"]


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_empty_list_registers_none(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    fake_mcp_runtime["session"] = _make_fake_session(["demo", "other"])
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {"test": MCPServerConfig(command="fake", enabled_tools=[])},
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == []


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_warns_on_unknown_entries(
    fake_mcp_runtime: dict[str, object | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mcp_runtime["session"] = _make_fake_session(["demo"])
    registry = ToolRegistry()
    warnings: list[str] = []

    def _warning(message: str, *args: object) -> None:
        warnings.append(message.format(*args))

    monkeypatch.setattr("hahobot.agent.tools.mcp.logger.warning", _warning)

    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {"test": MCPServerConfig(command="fake", enabled_tools=["unknown"])},
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == []
    assert warnings
    assert "enabledTools entries not found: unknown" in warnings[-1]
    assert "Available raw names: demo" in warnings[-1]
    assert "Available wrapped names: mcp_test_demo" in warnings[-1]


@pytest.mark.asyncio
async def test_connect_mcp_servers_registers_multiple_concurrently(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    """All servers connect; registration follows insertion order."""
    fake_mcp_runtime["session"] = _make_fake_session(["demo"])
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {
                "alpha": MCPServerConfig(command="a"),
                "beta": MCPServerConfig(command="b"),
            },
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == ["mcp_alpha_demo", "mcp_beta_demo"]


@pytest.mark.asyncio
async def test_connect_mcp_servers_skips_slow_server_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that hangs during connect is skipped on timeout; others still
    connect, and the slow one does not block the fast one (concurrent)."""
    import hahobot.agent.tools.mcp as mcp_module

    async def fake_open_session(name: str, cfg: object, stack: AsyncExitStack) -> object:
        if name == "slow":
            await asyncio.sleep(30)  # never resolves within the test
        return _make_fake_session(["demo"])

    monkeypatch.setattr(mcp_module, "_open_session", fake_open_session)

    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await asyncio.wait_for(
            connect_mcp_servers(
                {
                    "slow": MCPServerConfig(command="s", connect_timeout=0),
                    "fast": MCPServerConfig(command="f", connect_timeout=5),
                },
                registry,
                stack,
            ),
            timeout=5,  # whole call must finish well under the 30s slow sleep
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == ["mcp_fast_demo"]


@pytest.mark.asyncio
async def test_connection_owner_enters_and_exits_context_in_same_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hahobot.agent.tools.mcp as mcp_module

    context = _TaskAffineContext()

    async def fake_open_session(name: str, cfg: object, stack: AsyncExitStack) -> object:
        await stack.enter_async_context(context)
        return _make_fake_session(["demo"])

    monkeypatch.setattr(mcp_module, "_open_session", fake_open_session)
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    await connect_mcp_servers(
        {"test": MCPServerConfig(command="fake")},
        registry,
        stack,
    )

    wrapper = registry.get("mcp_test_demo")
    assert isinstance(wrapper, MCPToolWrapper)
    owner = wrapper._coordinator._owner
    assert context.enter_task is not asyncio.current_task()

    await owner.close()
    await owner.close()
    await stack.aclose()

    assert context.exit_task is context.enter_task
    assert context.exit_count == 1


@pytest.mark.asyncio
async def test_reconnect_rotates_task_owned_connection_generations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hahobot.agent.tools.mcp as mcp_module

    contexts: list[_TaskAffineContext] = []
    sessions: list[object] = []

    async def fake_open_session(name: str, cfg: object, stack: AsyncExitStack) -> object:
        context = _TaskAffineContext()
        contexts.append(context)
        await stack.enter_async_context(context)
        session = _make_fake_session(["demo"])
        sessions.append(session)
        return session

    monkeypatch.setattr(mcp_module, "_open_session", fake_open_session)
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    await connect_mcp_servers(
        {"test": MCPServerConfig(command="fake")},
        registry,
        stack,
    )

    wrapper = registry.get("mcp_test_demo")
    assert isinstance(wrapper, MCPToolWrapper)
    assert wrapper._coordinator is not None
    assert await wrapper._coordinator.reconnect(wrapper)

    assert len(contexts) == 2
    assert wrapper._session is sessions[1]
    assert contexts[0].exit_task is contexts[0].enter_task
    assert not contexts[1].exited.is_set()

    await stack.aclose()

    assert contexts[1].exit_task is contexts[1].enter_task
    assert [context.exit_count for context in contexts] == [1, 1]


@pytest.mark.asyncio
async def test_connection_timeout_closes_context_from_owner_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hahobot.agent.tools.mcp as mcp_module

    context = _TaskAffineContext()

    async def fake_open_session(name: str, cfg: object, stack: AsyncExitStack) -> object:
        await stack.enter_async_context(context)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(mcp_module, "_open_session", fake_open_session)
    cfg = SimpleNamespace(connect_timeout=0.01, enabled_tools=["*"], tool_timeout=30)
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers({"slow": cfg}, registry, stack)
    finally:
        await stack.aclose()

    assert registry.tool_names == []
    assert context.exited.is_set()
    assert context.exit_task is context.enter_task
    assert context.exit_count == 1


@pytest.mark.asyncio
async def test_registration_failure_closes_owner_without_leaking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hahobot.agent.tools.mcp as mcp_module

    context = _TaskAffineContext()

    async def fake_open_session(name: str, cfg: object, stack: AsyncExitStack) -> object:
        await stack.enter_async_context(context)
        return _make_fake_session(["demo"])

    class _FailingRegistry(ToolRegistry):
        def register(self, tool) -> None:
            super().register(tool)
            raise RuntimeError("registration failed")

    monkeypatch.setattr(mcp_module, "_open_session", fake_open_session)
    registry = _FailingRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {"test": MCPServerConfig(command="fake")},
            registry,
            stack,
        )
        assert context.exited.is_set()
        assert registry.tool_names == []
    finally:
        await stack.aclose()

    assert context.exit_task is context.enter_task
    assert context.exit_count == 1


@pytest.mark.asyncio
async def test_cancelled_connect_stops_independent_owner_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hahobot.agent.tools.mcp as mcp_module

    context = _TaskAffineContext()

    async def fake_open_session(name: str, cfg: object, stack: AsyncExitStack) -> object:
        await stack.enter_async_context(context)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(mcp_module, "_open_session", fake_open_session)
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    task = asyncio.create_task(
        connect_mcp_servers(
            {"test": MCPServerConfig(command="fake", connect_timeout=30)},
            registry,
            stack,
        )
    )
    await asyncio.wait_for(context.entered.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(context.exited.wait(), timeout=1)
    await stack.aclose()

    assert context.exit_task is context.enter_task
    assert context.exit_count == 1
