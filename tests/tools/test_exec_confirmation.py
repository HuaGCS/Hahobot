"""Regression coverage for one-shot shell execution approval."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hahobot.agent.commands.approval import ExecApprovalCommandHandler
from hahobot.agent.subagent import SubagentManager
from hahobot.agent.tools.exec_approval import ExecApprovalContext, ExecApprovalStore
from hahobot.agent.tools.registry import ToolRegistry
from hahobot.agent.tools.shell import ExecTool
from hahobot.agent.tools.spawn import SpawnTool
from hahobot.bus.events import InboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.config.schema import ExecToolConfig, WebToolsConfig
from hahobot.session.manager import Session


def _context(session_key: str = "cli:one", sender_id: str = "owner") -> ExecApprovalContext:
    return ExecApprovalContext(
        session_key=session_key,
        sender_id=sender_id,
        channel="cli",
        chat_id=session_key.split(":", 1)[-1],
    )


def _origin(
    *,
    session_key: str = "unified:default",
    sender_id: str = "owner",
    channel: str = "cli",
    chat_id: str = "one",
) -> ExecApprovalContext:
    return ExecApprovalContext(
        session_key=session_key,
        sender_id=sender_id,
        channel=channel,
        chat_id=chat_id,
    )


def _bind(
    tool: ExecTool,
    *,
    session_key: str = "cli:one",
    sender_id: str = "owner",
) -> None:
    tool.set_context("cli", session_key.split(":", 1)[-1], session_key, sender_id)


def _message(content: str, *, sender_id: str = "owner") -> InboundMessage:
    return InboundMessage(
        channel="cli",
        sender_id=sender_id,
        chat_id="one",
        content=content,
    )


def _fake_text(_language: str, key: str, **kwargs) -> str:
    if key == "approve_usage":
        return "usage"
    if key == "approve_no_pending":
        return "none"
    if key == "approve_exec_unavailable":
        return "exec unavailable"
    if key == "approve_results_header":
        return f"results={kwargs['count']}"
    return f"{key}:{kwargs['command']}:{kwargs['result']}"


def _agent_loop(tmp_path, *, max_tool_result_chars: int = 16_000):
    from hahobot.agent.loop import AgentLoop

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = MagicMock(max_tokens=4_096)
    with patch("hahobot.agent.loop.SubagentManager") as subagents:
        subagents.return_value.cancel_by_session = AsyncMock(return_value=0)
        return AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=tmp_path,
            max_tool_result_chars=max_tool_result_chars,
            exec_config=ExecToolConfig(confirmation_mode="always"),
            web_config=WebToolsConfig(enable=False),
        )


def test_config_defaults_to_model_while_raw_tool_stays_sdk_compatible() -> None:
    assert ExecToolConfig().confirmation_mode == "model"
    assert ExecTool().confirmation_mode == "allow"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "decision", "should_confirm"),
    [
        ("always", False, True),
        ("always", True, True),
        ("model", True, True),
        ("model", None, True),
        ("model", "false", True),
        ("model", 0, True),
        ("model", False, False),
        ("allow", True, False),
        ("allow", None, False),
    ],
)
async def test_confirmation_modes_fail_closed_for_missing_or_non_boolean_model_decisions(
    mode: str,
    decision,
    should_confirm: bool,
) -> None:
    store = ExecApprovalStore()
    tool = ExecTool(confirmation_mode=mode, approval_store=store)
    _bind(tool)
    execute = AsyncMock(return_value="ran")

    with patch.object(tool, "_execute_after_safety", execute):
        result = await tool.execute("echo safe", requires_confirmation=decision)

    if should_confirm:
        assert "Confirmation required" in result
        assert "/approve all" in result
        assert "does not change" in result
        execute.assert_not_awaited()
        assert [
            request.command
            for request in store.pending_for(session_key="cli:one", sender_id="owner")
        ] == ["echo safe"]
    else:
        assert result == "ran"
        execute.assert_awaited_once()
        assert store.pending_for(session_key="cli:one", sender_id="owner") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", ["false", "0", "no", 0, None, [], {}])
async def test_registry_never_casts_a_malformed_model_decision_into_authorization(
    malformed,
) -> None:
    store = ExecApprovalStore()
    tool = ExecTool(confirmation_mode="model", approval_store=store)
    _bind(tool)
    registry = ToolRegistry()
    registry.register(tool)
    spawn = AsyncMock()

    with patch.object(tool, "_spawn", spawn):
        result = await registry.execute(
            "exec",
            {"command": "echo guarded", "requires_confirmation": malformed},
        )

    assert "Confirmation required" in result
    spawn.assert_not_awaited()
    assert [
        request.command for request in store.pending_for(session_key="cli:one", sender_id="owner")
    ] == ["echo guarded"]


@pytest.mark.asyncio
async def test_confirmation_without_bound_sender_fails_closed_without_spawning() -> None:
    tool = ExecTool(confirmation_mode="always")
    spawn = AsyncMock()
    with patch.object(tool, "_spawn", spawn):
        result = await tool.execute("echo safe")
    assert "no session/sender context" in result
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_per_sender_queue_fails_closed_without_dropping_old_requests() -> None:
    store = ExecApprovalStore(max_pending_per_scope=2)
    tool = ExecTool(confirmation_mode="always", approval_store=store)
    _bind(tool)
    await tool.execute("echo one")
    await tool.execute("echo two")
    spawn = AsyncMock()

    with patch.object(tool, "_spawn", spawn):
        result = await tool.execute("echo three")

    assert "queue is full" in result
    assert "/approve all" in result
    spawn.assert_not_awaited()
    assert [
        request.command for request in store.pending_for(session_key="cli:one", sender_id="owner")
    ] == ["echo one", "echo two"]


def test_process_wide_queue_limit_bounds_rotating_scopes() -> None:
    store = ExecApprovalStore(max_pending_per_scope=32, max_pending_total=2)
    first = store.enqueue(
        command="echo one",
        working_dir="/tmp",
        timeout=None,
        context=_origin(session_key="cli:one", sender_id="one"),
    )
    second = store.enqueue(
        command="echo two",
        working_dir="/tmp",
        timeout=None,
        context=_origin(session_key="cli:two", sender_id="two"),
    )
    rejected = store.enqueue(
        command="echo three",
        working_dir="/tmp",
        timeout=None,
        context=_origin(session_key="cli:three", sender_id="three"),
    )

    assert first is not None and second is not None
    assert rejected is None


@pytest.mark.asyncio
async def test_confirmation_shows_exact_escaped_command_and_working_directory(tmp_path) -> None:
    store = ExecApprovalStore()
    tool = ExecTool(confirmation_mode="always", approval_store=store)
    _bind(tool)
    command = "printf 'first line'\necho hidden-suffix\t# end"

    result = await tool.execute(command, working_dir=str(tmp_path))

    assert len(result) <= store.max_result_chars
    assert result.index("/approve") < result.index("Command (exact JSON string)")
    assert "printf 'first line'\\necho hidden-suffix\\t# end" in result
    assert f'Working directory (exact JSON string): "{tmp_path}"' in result
    pending = store.pending_for(session_key="cli:one", sender_id="owner")
    assert pending[0].command == command
    assert pending[0].approval_preview.endswith(f'working_dir="{tmp_path}"')


@pytest.mark.asyncio
async def test_low_tool_result_budget_rejects_instead_of_hiding_exact_preview() -> None:
    store = ExecApprovalStore(max_result_chars=120)
    tool = ExecTool(confirmation_mode="always", approval_store=store)
    _bind(tool)
    spawn = AsyncMock()

    with patch.object(tool, "_spawn", spawn):
        result = await tool.execute("echo must-remain-visible")

    assert "exact approval preview exceeds" in result
    spawn.assert_not_awaited()
    assert store.pending_for(session_key="cli:one", sender_id="owner") == []


@pytest.mark.asyncio
async def test_agent_runtime_binds_confirmation_to_its_tool_result_budget(tmp_path) -> None:
    loop = _agent_loop(tmp_path, max_tool_result_chars=120)
    tool = loop.tools.get("exec")
    assert isinstance(tool, ExecTool)
    loop._set_tool_context(
        "cli",
        "one",
        session_key="cli:one",
        sender_id="owner",
        refresh_exec_approval=True,
    )

    result = await tool.execute("echo budgeted")

    assert loop.exec_approval_store.max_result_chars == 120
    assert "exact approval preview exceeds" in result
    assert loop.exec_approval_store.pending_for(session_key="cli:one", sender_id="owner") == []


@pytest.mark.asyncio
async def test_overlong_confirmation_is_rejected_instead_of_truncated() -> None:
    store = ExecApprovalStore(max_command_chars=8)
    tool = ExecTool(confirmation_mode="always", approval_store=store)
    _bind(tool)
    spawn = AsyncMock()

    with patch.object(tool, "_spawn", spawn):
        result = await tool.execute("echo too-long")

    assert "too long to present safely" in result
    spawn.assert_not_awaited()
    assert store.pending_for(session_key="cli:one", sender_id="owner") == []


@pytest.mark.asyncio
async def test_admission_guards_run_before_a_request_is_queued() -> None:
    store = ExecApprovalStore()
    tool = ExecTool(
        confirmation_mode="always",
        approval_store=store,
        allow_patterns=[r"echo .*"],
    )
    _bind(tool)

    dangerous = await tool.execute("rm -rf build")
    outside_allowlist = await tool.execute("git status")
    with patch(
        "hahobot.security.network.contains_internal_url",
        AsyncMock(return_value=True),
    ):
        internal = await tool.execute("echo http://127.0.0.1/private")

    assert "dangerous" in dangerous
    assert "allowlist" in outside_allowlist
    assert "internal/private URL" in internal
    assert store.pending_for(session_key="cli:one", sender_id="owner") == []


def test_store_scopes_oldest_and_all_to_session_and_sender_and_expires() -> None:
    now = [100.0]
    store = ExecApprovalStore(ttl_seconds=5, clock=lambda: now[0])
    owner = _context()
    other_sender = _context(sender_id="guest")
    other_session = _context(session_key="cli:two")
    first = store.enqueue(command="echo first", working_dir="/tmp", timeout=None, context=owner)
    second = store.enqueue(command="echo second", working_dir="/tmp", timeout=None, context=owner)
    store.enqueue(command="echo guest", working_dir="/tmp", timeout=None, context=other_sender)
    store.enqueue(command="echo other", working_dir="/tmp", timeout=None, context=other_session)
    assert first is not None and second is not None

    assert store.consume(session_key="cli:one", sender_id="owner") == [first]
    assert store.consume(session_key="cli:one", sender_id="owner", all_pending=True) == [second]
    assert [
        request.command for request in store.pending_for(session_key="cli:one", sender_id="guest")
    ] == ["echo guest"]
    assert [
        request.command for request in store.pending_for(session_key="cli:two", sender_id="owner")
    ] == ["echo other"]

    now[0] = 106.0
    assert store.pending_for(session_key="cli:one", sender_id="guest") == []
    assert store.pending_for(session_key="cli:two", sender_id="owner") == []


@pytest.mark.asyncio
async def test_context_var_keeps_concurrent_sessions_separate() -> None:
    store = ExecApprovalStore()
    tool = ExecTool(confirmation_mode="always", approval_store=store)
    first_bound = asyncio.Event()
    second_done = asyncio.Event()

    async def first() -> None:
        _bind(tool, session_key="cli:first", sender_id="alice")
        first_bound.set()
        await second_done.wait()
        await tool.execute("echo first")

    async def second() -> None:
        await first_bound.wait()
        _bind(tool, session_key="cli:second", sender_id="bob")
        await tool.execute("echo second")
        second_done.set()

    await asyncio.gather(first(), second())

    assert [
        request.command for request in store.pending_for(session_key="cli:first", sender_id="alice")
    ] == ["echo first"]
    assert [
        request.command for request in store.pending_for(session_key="cli:second", sender_id="bob")
    ] == ["echo second"]


@pytest.mark.asyncio
async def test_session_generation_blocks_delayed_enqueue_after_clear() -> None:
    store = ExecApprovalStore()
    tool = ExecTool(confirmation_mode="always", approval_store=store)
    _bind(tool)
    stale = store.current_context()
    assert stale is not None

    store.clear_session("cli:one")
    tool.bind_approval_context(stale)
    stale_result = await tool.execute("echo stale")

    assert "session state that was reset" in stale_result
    assert store.pending_for(session_key="cli:one", sender_id="owner") == []

    tool.set_context("cli", "one", "cli:one", "owner", refresh=True)
    fresh_result = await tool.execute("echo fresh")
    assert "Confirmation required" in fresh_result
    assert [
        request.command for request in store.pending_for(session_key="cli:one", sender_id="owner")
    ] == ["echo fresh"]


@pytest.mark.asyncio
async def test_loop_hook_style_rebind_does_not_refresh_a_cleared_turn(tmp_path) -> None:
    loop = _agent_loop(tmp_path)
    tool = loop.tools.get("exec")
    assert isinstance(tool, ExecTool)
    loop._set_tool_context(
        "cli",
        "one",
        session_key="cli:one",
        sender_id="owner",
        refresh_exec_approval=True,
    )
    captured = loop.exec_approval_store.current_context()
    assert captured is not None

    loop.exec_approval_store.clear_session("cli:one")
    # LoopRunHook calls the same setter before every tool batch. It must preserve
    # the turn's captured generation instead of silently reviving the old turn.
    loop._set_tool_context(
        "cli",
        "one",
        session_key="cli:one",
        sender_id="owner",
    )

    assert loop.exec_approval_store.current_context() == captured
    result = await tool.execute("echo delayed-main-turn")
    assert "session state that was reset" in result


@pytest.mark.asyncio
async def test_spawn_context_var_keeps_subagent_origins_separate() -> None:
    manager = MagicMock(spec=SubagentManager)
    manager.spawn = AsyncMock(return_value="started")
    tool = SpawnTool(manager)
    first_bound = asyncio.Event()
    second_done = asyncio.Event()

    async def first() -> None:
        tool.set_context("cli", "first", "cli:first", "alice")
        first_bound.set()
        await second_done.wait()
        await tool.execute("first task")

    async def second() -> None:
        await first_bound.wait()
        tool.set_context("telegram", "second", "unified:default", "bob")
        await tool.execute("second task")
        second_done.set()

    await asyncio.gather(first(), second())

    calls = [call.kwargs for call in manager.spawn.await_args_list]
    assert calls[0]["origin_channel"] == "telegram"
    assert calls[0]["session_key"] == "unified:default"
    assert calls[0]["sender_id"] == "bob"
    assert calls[1]["origin_channel"] == "cli"
    assert calls[1]["session_key"] == "cli:first"
    assert calls[1]["sender_id"] == "alice"


@pytest.mark.asyncio
async def test_approval_rechecks_current_guards_before_spawning() -> None:
    store = ExecApprovalStore()
    tool = ExecTool(confirmation_mode="always", approval_store=store)
    _bind(tool)
    await tool.execute("echo safe")
    request = store.consume(session_key="cli:one", sender_id="owner")[0]
    tool.allow_patterns = [r"git status"]
    spawn = AsyncMock()

    with patch.object(tool, "_spawn", spawn):
        result = await tool.execute_approved(request)

    assert "allowlist" in result
    spawn.assert_not_awaited()
    assert store.consume(session_key="cli:one", sender_id="owner") == []


@pytest.mark.asyncio
async def test_approve_is_one_shot_and_approve_all_uses_a_queue_snapshot(monkeypatch) -> None:
    monkeypatch.setattr("hahobot.agent.commands.approval.text", _fake_text)
    store = ExecApprovalStore()
    tool = ExecTool(confirmation_mode="always", approval_store=store)
    registry = ToolRegistry()
    registry.register(tool)
    loop = SimpleNamespace(
        exec_approval_store=store,
        tools=registry,
        _get_session_language=lambda _session: "en",
    )
    handler = ExecApprovalCommandHandler(loop)
    session = Session(key="cli:one")
    owner = _context()
    store.enqueue(command="echo one", working_dir="/tmp", timeout=None, context=owner)
    store.enqueue(command="echo two", working_dir="/tmp", timeout=None, context=owner)
    calls: list[str] = []

    async def execute(command: str, *, cwd: str, timeout: int | None) -> str:
        calls.append(command)
        if command == "echo one":
            store.enqueue(command="echo late", working_dir="/tmp", timeout=None, context=owner)
        return "ok\nExit code: 0"

    monkeypatch.setattr(tool, "_execute_after_safety", execute)
    response = await handler.handle(_message("/approve all"), session)

    assert calls == ["echo one", "echo two"]
    assert response.content.startswith("results=2")
    assert 'command="echo one"\nworking_dir="/tmp"' in response.content
    assert [
        request.command for request in store.pending_for(session_key="cli:one", sender_id="owner")
    ] == ["echo late"]

    await handler.handle(_message("/approve"), session)
    assert calls == ["echo one", "echo two", "echo late"]
    empty = await handler.handle(_message("/approve"), session)
    assert empty.content == "none"


@pytest.mark.asyncio
async def test_duplicate_pending_exec_is_approved_and_executed_only_once(monkeypatch) -> None:
    monkeypatch.setattr("hahobot.agent.commands.approval.text", _fake_text)
    store = ExecApprovalStore()
    tool = ExecTool(confirmation_mode="always", approval_store=store)
    _bind(tool)
    first = await tool.execute("echo idempotent")
    second = await tool.execute("echo idempotent")
    pending = store.pending_for(session_key="cli:one", sender_id="owner")
    assert len(pending) == 1
    assert pending[0].request_id in first
    assert pending[0].request_id in second

    registry = ToolRegistry()
    registry.register(tool)
    handler = ExecApprovalCommandHandler(
        SimpleNamespace(
            exec_approval_store=store,
            tools=registry,
            _get_session_language=lambda _session: "en",
        )
    )
    execute = AsyncMock(return_value="ok\nExit code: 0")
    monkeypatch.setattr(tool, "_execute_after_safety", execute)

    response = await handler.handle(_message("/approve all"), Session(key="cli:one"))

    assert response.content.startswith("results=1")
    execute.assert_awaited_once()
    assert store.pending_for(session_key="cli:one", sender_id="owner") == []


@pytest.mark.asyncio
async def test_approve_cannot_consume_another_sender_request(monkeypatch) -> None:
    monkeypatch.setattr("hahobot.agent.commands.approval.text", _fake_text)
    store = ExecApprovalStore()
    registry = ToolRegistry()
    registry.register(ExecTool(confirmation_mode="allow", approval_store=store))
    handler = ExecApprovalCommandHandler(
        SimpleNamespace(
            exec_approval_store=store,
            tools=registry,
            _get_session_language=lambda _session: "en",
        )
    )
    session = Session(key="cli:one")
    store.enqueue(
        command="echo private",
        working_dir="/tmp",
        timeout=None,
        context=_context(sender_id="alice"),
    )

    denied = await handler.handle(_message("/approve", sender_id="bob"), session)

    assert denied.content == "none"
    assert len(store.pending_for(session_key="cli:one", sender_id="alice")) == 1


@pytest.mark.asyncio
async def test_approve_cannot_cross_chat_or_channel_inside_a_unified_session(monkeypatch) -> None:
    monkeypatch.setattr("hahobot.agent.commands.approval.text", _fake_text)
    store = ExecApprovalStore()
    registry = ToolRegistry()
    registry.register(ExecTool(confirmation_mode="allow", approval_store=store))
    handler = ExecApprovalCommandHandler(
        SimpleNamespace(
            exec_approval_store=store,
            tools=registry,
            _get_session_language=lambda _session: "en",
        )
    )
    session = Session(key="unified:default")
    store.enqueue(
        command="echo telegram-one",
        working_dir="/tmp",
        timeout=None,
        context=_origin(channel="telegram", chat_id="one"),
    )

    wrong_chat = InboundMessage(
        channel="telegram",
        sender_id="owner",
        chat_id="two",
        content="/approve",
    )
    wrong_channel = InboundMessage(
        channel="cli",
        sender_id="owner",
        chat_id="one",
        content="/approve",
    )
    assert (await handler.handle(wrong_chat, session)).content == "none"
    assert (await handler.handle(wrong_channel, session)).content == "none"
    assert (
        len(
            store.pending_for(
                session_key="unified:default",
                sender_id="owner",
                channel="telegram",
                chat_id="one",
            )
        )
        == 1
    )


def test_nonzero_exit_code_is_rendered_as_an_approval_failure() -> None:
    assert ExecApprovalCommandHandler._failed("output\nExit code: 0") is False
    assert ExecApprovalCommandHandler._failed("Error: stdout only\nExit code: 0") is False
    assert ExecApprovalCommandHandler._failed("output\nExit code: 2") is True
    assert ExecApprovalCommandHandler._failed("Error: blocked") is True


@pytest.mark.asyncio
async def test_agent_loop_routes_one_shot_approval_without_changing_config(tmp_path) -> None:
    loop = _agent_loop(tmp_path)
    tool = loop.tools.get("exec")
    assert isinstance(tool, ExecTool)
    loop._set_tool_context(
        "cli",
        "one",
        session_key="cli:one",
        sender_id="owner",
    )
    await tool.execute("echo routed")
    execute = AsyncMock(return_value="routed\nExit code: 0")

    with patch.object(tool, "_execute_after_safety", execute):
        response = await loop._process_message(_message("/approve"))

    assert response is not None
    assert "echo routed" in response.content
    execute.assert_awaited_once()
    assert loop.exec_config.confirmation_mode == "always"
    assert loop.exec_approval_store.pending_for(session_key="cli:one", sender_id="owner") == []


@pytest.mark.asyncio
async def test_hot_reload_disabling_exec_makes_pending_approval_fail_without_spawning(
    tmp_path,
) -> None:
    loop = _agent_loop(tmp_path)
    tool = loop.tools.get("exec")
    assert isinstance(tool, ExecTool)
    loop._set_tool_context(
        "cli",
        "one",
        session_key="cli:one",
        sender_id="owner",
        refresh_exec_approval=True,
    )
    await tool.execute("echo disabled-before-approval")
    spawn = AsyncMock()
    with patch.object(tool, "_spawn", spawn):
        loop.exec_config = ExecToolConfig(enable=False)
        loop._apply_runtime_tool_config()
        response = await loop._process_message(_message("/approve"))

    assert response is not None
    assert "disabled" in response.content.lower()
    spawn.assert_not_awaited()
    assert loop.exec_approval_store.pending_for(session_key="cli:one", sender_id="owner") == []


def test_trusted_subagent_result_preserves_origin_sender_for_approval(tmp_path) -> None:
    loop = _agent_loop(tmp_path)
    trusted = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:one",
        content="done",
        metadata={
            "injected_event": "subagent_result",
            "origin_sender_id": "owner",
        },
    )
    spoofed = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:one",
        content="done",
        metadata={"origin_sender_id": "owner"},
    )

    assert loop._exec_approval_sender_id(trusted) == "owner"
    assert loop._exec_approval_sender_id(spoofed) == "subagent"


@pytest.mark.asyncio
async def test_subagent_announcement_carries_internal_origin_sender(tmp_path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    bus = MessageBus()
    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=4_000,
    )

    await manager._announce_result(
        "task-1",
        "label",
        "task",
        "done",
        {
            "channel": "cli",
            "chat_id": "one",
            "session_key": "cli:one",
            "sender_id": "owner",
        },
        "ok",
    )
    inbound = await bus.consume_inbound()

    assert inbound.sender_id == "subagent"
    assert inbound.metadata["injected_event"] == "subagent_result"
    assert inbound.metadata["origin_sender_id"] == "owner"


@pytest.mark.asyncio
async def test_session_reset_persona_switch_and_stop_clear_pending_approvals(tmp_path) -> None:
    loop = _agent_loop(tmp_path)
    tool = loop.tools.get("exec")
    assert isinstance(tool, ExecTool)
    loop._set_tool_context(
        "cli",
        "one",
        session_key="cli:one",
        sender_id="owner",
    )

    await tool.execute("echo before-new")
    await loop._process_message(_message("/new"))
    assert loop.exec_approval_store.pending_for(session_key="cli:one", sender_id="owner") == []

    (tmp_path / "personas" / "Coder").mkdir(parents=True)
    await tool.execute("echo before-persona")
    session = loop.sessions.get_or_create("cli:one")
    await loop._persona_commands.set(_message("/persona set Coder"), session, "Coder")
    assert loop.exec_approval_store.pending_for(session_key="cli:one", sender_id="owner") == []

    await tool.execute("echo before-stop")
    await loop._handle_stop(_message("/stop"))
    assert loop.exec_approval_store.pending_for(session_key="cli:one", sender_id="owner") == []


@pytest.mark.asyncio
async def test_subagent_exec_shares_the_runtime_approval_store(tmp_path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    store = ExecApprovalStore()
    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4_000,
        web_enabled=False,
        exec_config=ExecToolConfig(confirmation_mode="model"),
        approval_store=store,
    )
    tools = manager._build_tools_for_mode(
        "implement",
        origin={
            "channel": "cli",
            "chat_id": "one",
            "session_key": "cli:one",
            "sender_id": "owner",
        },
    )
    exec_tool = tools.get("exec")

    assert isinstance(exec_tool, ExecTool)
    assert exec_tool.approval_store is store
    assert exec_tool.confirmation_mode == "model"
    await exec_tool.execute("echo subagent", requires_confirmation=True)
    assert [
        request.command for request in store.pending_for(session_key="cli:one", sender_id="owner")
    ] == ["echo subagent"]

    manager.apply_runtime_config(
        workspace=tmp_path,
        model="test-model",
        brave_api_key=None,
        web_proxy=None,
        web_enabled=False,
        web_search_provider="brave",
        web_search_base_url=None,
        web_search_max_results=5,
        exec_config=ExecToolConfig(confirmation_mode="allow"),
        restrict_to_workspace=False,
        disabled_skills=[],
    )
    assert exec_tool.confirmation_mode == "allow"


@pytest.mark.asyncio
async def test_subagent_captured_before_session_clear_cannot_requeue_afterward(tmp_path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    store = ExecApprovalStore()
    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4_000,
        web_enabled=False,
        exec_config=ExecToolConfig(confirmation_mode="always"),
        approval_store=store,
    )
    captured = store.make_context(
        session_key="cli:one",
        sender_id="owner",
        channel="cli",
        chat_id="one",
    )
    tools = manager._build_tools_for_mode(
        "implement",
        origin={
            "channel": "cli",
            "chat_id": "one",
            "session_key": "cli:one",
            "sender_id": "owner",
            "approval_context": captured,
        },
    )
    exec_tool = tools.get("exec")
    assert isinstance(exec_tool, ExecTool)

    store.clear_session("cli:one")
    result = await exec_tool.execute("echo delayed")

    assert "session state that was reset" in result
    assert store.pending_for(session_key="cli:one", sender_id="owner") == []
