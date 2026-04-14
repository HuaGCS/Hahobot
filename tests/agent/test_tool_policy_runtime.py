from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hahobot.agent.loop import AgentLoop
from hahobot.agent.subagent import SubagentManager
from hahobot.bus.queue import MessageBus
from hahobot.config.schema import ExecToolConfig, WebToolsConfig


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = MagicMock(max_tokens=4096)
    return provider


def test_agent_loop_does_not_register_web_tools_when_disabled(tmp_path) -> None:
    with patch("hahobot.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=MessageBus(),
            provider=_provider(),
            workspace=tmp_path,
            web_config=WebToolsConfig(enable=False),
        )

    assert loop.tools.get("web_search") is None
    assert loop.tools.get("web_fetch") is None


def test_apply_runtime_tool_config_toggles_exec_and_web_tools(tmp_path) -> None:
    with patch("hahobot.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=MessageBus(),
            provider=_provider(),
            workspace=tmp_path,
            web_config=WebToolsConfig(enable=False),
            exec_config=ExecToolConfig(enable=False),
        )

    assert loop.tools.get("exec") is None
    assert loop.tools.get("web_search") is None
    assert loop.tools.get("web_fetch") is None


def test_duckduckgo_web_policy_is_ready_without_extra_config(tmp_path) -> None:
    with patch("hahobot.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=MessageBus(),
            provider=_provider(),
            workspace=tmp_path,
            web_config=WebToolsConfig.model_validate(
                {"enable": True, "search": {"provider": "duckduckgo"}}
            ),
        )

    decision = loop._tool_policy().web()
    assert decision.status == "ok"
    assert "provider=duckduckgo" in decision.detail
    assert "serialized=true" in decision.detail

    loop.exec_config = ExecToolConfig(enable=True, allowed_env_keys=["JAVA_HOME"])
    loop.web_enabled = True
    loop.web_search_provider = "searxng"
    loop.web_search_base_url = "http://localhost:8080"
    loop._apply_runtime_tool_config()

    exec_tool = loop.tools.get("exec")
    assert exec_tool is not None
    assert exec_tool.allowed_env_keys == ["JAVA_HOME"]
    assert loop.tools.get("web_search") is not None
    assert loop.tools.get("web_fetch") is not None

    loop.exec_config = ExecToolConfig(enable=False)
    loop.web_enabled = False
    loop._apply_runtime_tool_config()

    assert loop.tools.get("exec") is None
    assert loop.tools.get("web_search") is None
    assert loop.tools.get("web_fetch") is None


@pytest.mark.asyncio
async def test_subagent_manager_respects_disabled_web_tools(tmp_path) -> None:
    mgr = SubagentManager(
        provider=_provider(),
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4000,
        web_enabled=False,
    )
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        assert spec.tools.get("web_search") is None
        assert spec.tools.get("web_fetch") is None
        return SimpleNamespace(
            stop_reason="done",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    await mgr._run_subagent("sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"})

    mgr.runner.run.assert_awaited_once()
