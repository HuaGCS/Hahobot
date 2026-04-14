"""Tests for /restart slash command."""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hahobot.bus.events import InboundMessage
from hahobot.providers.base import LLMResponse
from hahobot.utils.self_update import SelfUpdateCheckResult, SelfUpdateError


def _make_loop():
    """Create a minimal AgentLoop with mocked dependencies."""
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    workspace = MagicMock()
    workspace.__truediv__ = MagicMock(return_value=MagicMock())

    with patch("hahobot.agent.loop.ContextBuilder"), \
         patch("hahobot.agent.loop.SessionManager"), \
         patch("hahobot.agent.loop.SubagentManager"):
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace)
    return loop, bus


class TestRestartCommand:

    @pytest.mark.asyncio
    async def test_restart_sends_message_and_calls_execv(self):
        from hahobot.command.builtin import cmd_restart
        from hahobot.command.router import CommandContext
        from hahobot.utils.restart import (
            RESTART_NOTIFY_CHANNEL_ENV,
            RESTART_NOTIFY_CHAT_ID_ENV,
            RESTART_STARTED_AT_ENV,
        )

        loop, bus = _make_loop()
        msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/restart")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/restart", loop=loop)

        with patch.dict(os.environ, {}, clear=False), \
             patch("hahobot.command.builtin.os.execv") as mock_execv:
            out = await cmd_restart(ctx)
            assert "Restarting" in out.content
            assert os.environ.get(RESTART_NOTIFY_CHANNEL_ENV) == "cli"
            assert os.environ.get(RESTART_NOTIFY_CHAT_ID_ENV) == "direct"
            assert os.environ.get(RESTART_STARTED_AT_ENV)

            await asyncio.sleep(1.5)
            mock_execv.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_intercepted_in_run_loop(self):
        """Verify /restart is handled at the run-loop level, not inside _dispatch."""
        loop, bus = _make_loop()
        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/restart")

        with patch.object(loop, "_dispatch", new_callable=AsyncMock) as mock_dispatch, \
             patch("hahobot.command.builtin.os.execv"):
            await bus.publish_inbound(msg)

            loop._running = True
            run_task = asyncio.create_task(loop.run())
            await asyncio.sleep(0.1)
            loop._running = False
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

            mock_dispatch.assert_not_called()
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "Restarting" in out.content

    @pytest.mark.asyncio
    async def test_status_intercepted_in_run_loop(self):
        """Verify /status is handled at the run-loop level for immediate replies."""
        loop, bus = _make_loop()
        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/status")

        with patch.object(loop, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await bus.publish_inbound(msg)

            loop._running = True
            run_task = asyncio.create_task(loop.run())
            await asyncio.sleep(0.1)
            loop._running = False
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

            mock_dispatch.assert_not_called()
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "hahobot" in out.content.lower() or "Model" in out.content

    @pytest.mark.asyncio
    async def test_run_propagates_external_cancellation(self):
        """External task cancellation should not be swallowed by the inbound wait loop."""
        loop, _bus = _make_loop()

        run_task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.1)
        run_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_help_includes_restart(self):
        loop, bus = _make_loop()
        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/help")

        response = await loop._process_message(msg)

        assert response is not None
        assert "/restart" in response.content
        assert "/update" in response.content
        assert "/status" in response.content
        assert response.metadata == {"render_as": "text"}

    @pytest.mark.asyncio
    async def test_update_runs_in_background_and_restarts_on_success(self, monkeypatch):
        from hahobot.command.builtin import cmd_update
        from hahobot.command.router import CommandContext
        from hahobot.utils.restart import (
            RESTART_NOTIFY_CHANNEL_ENV,
            RESTART_NOTIFY_CHAT_ID_ENV,
            RESTART_STARTED_AT_ENV,
        )

        loop, bus = _make_loop()
        msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/update")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/update", loop=loop)
        seen: dict[str, object] = {}

        async def fake_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        def fake_update(*, channels_config, language, force, bridge_only):
            seen["language"] = language
            seen["channels_config"] = channels_config
            seen["force"] = force
            seen["bridge_only"] = bridge_only

        monkeypatch.setattr("hahobot.command.builtin.asyncio.to_thread", fake_to_thread)
        monkeypatch.setattr("hahobot.command.builtin.perform_self_update", fake_update)

        with patch.dict(os.environ, {}, clear=False), \
             patch("hahobot.command.builtin.os.execv") as mock_execv:
            out = await cmd_update(ctx)
            assert "update" in out.content.lower()

            follow_up = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "Restarting" in follow_up.content
            assert seen["language"] == "en"
            assert seen["force"] is False
            assert seen["bridge_only"] is False
            assert os.environ.get(RESTART_NOTIFY_CHANNEL_ENV) == "cli"
            assert os.environ.get(RESTART_NOTIFY_CHAT_ID_ENV) == "direct"
            assert os.environ.get(RESTART_STARTED_AT_ENV)
            await asyncio.sleep(1.1)
            mock_execv.assert_called_once()
            if loop._background_tasks:
                await asyncio.wait_for(
                    asyncio.gather(*tuple(loop._background_tasks), return_exceptions=True),
                    timeout=1.0,
                )

    @pytest.mark.asyncio
    async def test_update_force_passes_force_flag(self, monkeypatch):
        from hahobot.command.builtin import cmd_update
        from hahobot.command.router import CommandContext

        loop, bus = _make_loop()
        msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/update force")
        ctx = CommandContext(
            msg=msg,
            session=None,
            key=msg.session_key,
            raw="/update force",
            args="force",
            loop=loop,
        )
        seen: dict[str, object] = {}

        async def fake_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        def fake_update(*, channels_config, language, force, bridge_only):
            seen["force"] = force
            seen["bridge_only"] = bridge_only

        monkeypatch.setattr("hahobot.command.builtin.asyncio.to_thread", fake_to_thread)
        monkeypatch.setattr("hahobot.command.builtin.perform_self_update", fake_update)

        with patch("hahobot.command.builtin.os.execv"):
            out = await cmd_update(ctx)
            assert "force" in out.content.lower()
            await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            await asyncio.sleep(1.1)
            if loop._background_tasks:
                await asyncio.wait_for(
                    asyncio.gather(*tuple(loop._background_tasks), return_exceptions=True),
                    timeout=1.0,
                )

        assert seen == {"force": True, "bridge_only": False}

    @pytest.mark.asyncio
    async def test_update_bridge_passes_bridge_flag(self, monkeypatch):
        from hahobot.command.builtin import cmd_update
        from hahobot.command.router import CommandContext

        loop, bus = _make_loop()
        msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/update bridge")
        ctx = CommandContext(
            msg=msg,
            session=None,
            key=msg.session_key,
            raw="/update bridge",
            args="bridge",
            loop=loop,
        )
        seen: dict[str, object] = {}

        async def fake_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        def fake_update(*, channels_config, language, force, bridge_only):
            seen["force"] = force
            seen["bridge_only"] = bridge_only

        monkeypatch.setattr("hahobot.command.builtin.asyncio.to_thread", fake_to_thread)
        monkeypatch.setattr("hahobot.command.builtin.perform_self_update", fake_update)

        with patch("hahobot.command.builtin.os.execv"):
            out = await cmd_update(ctx)
            assert "bridge" in out.content.lower()
            follow_up = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "bridge" in follow_up.content.lower()
            await asyncio.sleep(1.1)
            if loop._background_tasks:
                await asyncio.wait_for(
                    asyncio.gather(*tuple(loop._background_tasks), return_exceptions=True),
                    timeout=1.0,
                )

        assert seen == {"force": False, "bridge_only": True}

    @pytest.mark.asyncio
    async def test_update_reports_failure_without_restarting(self, monkeypatch):
        from hahobot.command.builtin import cmd_update
        from hahobot.command.router import CommandContext

        loop, bus = _make_loop()
        msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/update")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/update", loop=loop)

        async def fake_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        def fake_update(*, channels_config, language, force, bridge_only):
            raise SelfUpdateError("dirty worktree")

        monkeypatch.setattr("hahobot.command.builtin.asyncio.to_thread", fake_to_thread)
        monkeypatch.setattr("hahobot.command.builtin.perform_self_update", fake_update)

        with patch("hahobot.command.builtin.os.execv") as mock_execv:
            out = await cmd_update(ctx)
            assert "update" in out.content.lower()

            follow_up = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "Update failed" in follow_up.content
            assert "dirty worktree" in follow_up.content
            mock_execv.assert_not_called()
            if loop._background_tasks:
                await asyncio.wait_for(
                    asyncio.gather(*tuple(loop._background_tasks), return_exceptions=True),
                    timeout=1.0,
                )

    @pytest.mark.asyncio
    async def test_update_check_returns_rendered_report(self, monkeypatch):
        from hahobot.command.builtin import cmd_update
        from hahobot.command.router import CommandContext

        loop, _bus = _make_loop()
        msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/update check")
        ctx = CommandContext(
            msg=msg,
            session=None,
            key=msg.session_key,
            raw="/update check",
            args="check",
            loop=loop,
        )
        result = SelfUpdateCheckResult(
            mode="full",
            project_root=None,
            repo_root=None,
            branch="main",
            upstream="origin/main",
            worktree_clean=True,
            dirty_changes="",
            bridge_required=False,
            git_available=True,
            uv_available=True,
            npm_available=None,
            issues=(),
        )

        async def fake_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        monkeypatch.setattr("hahobot.command.builtin.asyncio.to_thread", fake_to_thread)
        monkeypatch.setattr("hahobot.command.builtin.inspect_self_update", lambda **kwargs: result)
        monkeypatch.setattr(
            "hahobot.command.builtin.format_self_update_check",
            lambda payload, language=None: f"report:{payload.mode}:{language}",
        )

        out = await cmd_update(ctx)

        assert out.content == "report:full:en"
        assert out.metadata == {"render_as": "text"}
        assert not loop._background_tasks

    @pytest.mark.asyncio
    async def test_update_unknown_subcommand_returns_usage(self):
        from hahobot.command.builtin import cmd_update
        from hahobot.command.router import CommandContext

        loop, _bus = _make_loop()
        msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/update nope")
        ctx = CommandContext(
            msg=msg,
            session=None,
            key=msg.session_key,
            raw="/update nope",
            args="nope",
            loop=loop,
        )

        out = await cmd_update(ctx)

        assert "Unknown /update subcommand" in out.content
        assert "/update check" in out.content
        assert out.metadata == {"render_as": "text"}

    @pytest.mark.asyncio
    async def test_status_reports_runtime_info(self):
        loop, _bus = _make_loop()
        session = MagicMock()
        session.get_history.return_value = [{"role": "user"}] * 3
        loop.sessions.get_or_create.return_value = session
        loop._start_time = time.time() - 125
        loop._last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        loop.consolidator.estimate_session_prompt_tokens = MagicMock(
            return_value=(20500, "tiktoken")
        )

        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/status")

        response = await loop._process_message(msg)

        assert response is not None
        assert "Model: test-model" in response.content
        assert "Tokens: 0 in / 0 out" in response.content
        assert "Context: 20k/65k (31%)" in response.content
        assert "Session: 3 messages" in response.content
        assert "Uptime: 2m 5s" in response.content
        assert response.metadata == {"render_as": "text"}

    @pytest.mark.asyncio
    async def test_run_agent_loop_resets_usage_when_provider_omits_it(self):
        loop, _bus = _make_loop()
        loop.provider.chat_with_retry = AsyncMock(side_effect=[
            LLMResponse(content="first", usage={"prompt_tokens": 9, "completion_tokens": 4}),
            LLMResponse(content="second", usage={}),
        ])

        await loop._run_agent_loop([])
        assert loop._last_usage["prompt_tokens"] == 9
        assert loop._last_usage["completion_tokens"] == 4

        await loop._run_agent_loop([])
        assert loop._last_usage["prompt_tokens"] == 0
        assert loop._last_usage["completion_tokens"] == 0

    @pytest.mark.asyncio
    async def test_status_falls_back_to_last_usage_when_context_estimate_missing(self):
        loop, _bus = _make_loop()
        session = MagicMock()
        session.get_history.return_value = [{"role": "user"}]
        loop.sessions.get_or_create.return_value = session
        loop._last_usage = {"prompt_tokens": 1200, "completion_tokens": 34}
        loop.consolidator.estimate_session_prompt_tokens = MagicMock(
            return_value=(0, "none")
        )

        response = await loop._process_message(
            InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/status")
        )

        assert response is not None
        assert "Tokens: 1200 in / 34 out" in response.content
        assert "Context: 1k/65k (1%)" in response.content

    @pytest.mark.asyncio
    async def test_process_direct_preserves_render_metadata(self):
        loop, _bus = _make_loop()
        session = MagicMock()
        session.get_history.return_value = []
        loop.sessions.get_or_create.return_value = session
        loop.subagents.get_running_count.return_value = 0

        response = await loop.process_direct("/status", session_key="cli:test")

        assert response is not None
        assert response.metadata == {"render_as": "text"}
