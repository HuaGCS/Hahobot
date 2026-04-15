from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hahobot.agent.loop import AgentLoop
from hahobot.agent.subagent import SubagentManager
from hahobot.bus.queue import MessageBus
from hahobot.config.schema import ExecToolConfig, ImageGenConfig, WebToolsConfig


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


def test_apply_runtime_tool_config_updates_existing_tool_instances(tmp_path) -> None:
    with patch("hahobot.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=MessageBus(),
            provider=_provider(),
            workspace=tmp_path,
            web_config=WebToolsConfig.model_validate(
                {"enable": True, "proxy": "http://proxy-a", "search": {"provider": "duckduckgo"}}
            ),
            exec_config=ExecToolConfig(
                enable=True,
                timeout=30,
                sandbox="sandbox-a",
                path_append="/opt/a",
                allowed_env_keys=["JAVA_HOME"],
            ),
        )

    exec_tool = loop.tools.get("exec")
    web_search_tool = loop.tools.get("web_search")
    web_fetch_tool = loop.tools.get("web_fetch")

    loop.exec_config = ExecToolConfig(
        enable=True,
        timeout=45,
        sandbox="sandbox-b",
        path_append="/opt/b",
        allowed_env_keys=["GOPATH"],
    )
    loop.web_enabled = True
    loop.web_proxy = "http://proxy-b"
    loop.web_search_provider = "searxng"
    loop.web_search_base_url = "http://localhost:8080"
    loop.web_search_max_results = 7

    loop._apply_runtime_tool_config()

    assert loop.tools.get("exec") is exec_tool
    assert exec_tool is not None
    assert exec_tool.timeout == 45
    assert exec_tool.sandbox == "sandbox-b"
    assert exec_tool.path_append == "/opt/b"
    assert exec_tool.allowed_env_keys == ["GOPATH"]

    assert loop.tools.get("web_search") is web_search_tool
    assert web_search_tool is not None
    assert web_search_tool.provider == "searxng"
    assert web_search_tool.base_url == "http://localhost:8080"
    assert web_search_tool.max_results == 7
    assert web_search_tool.proxy == "http://proxy-b"

    assert loop.tools.get("web_fetch") is web_fetch_tool
    assert web_fetch_tool is not None
    assert web_fetch_tool.proxy == "http://proxy-b"


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


def test_apply_runtime_tool_config_updates_existing_image_gen_tool(tmp_path) -> None:
    with patch("hahobot.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=MessageBus(),
            provider=_provider(),
            workspace=tmp_path,
            image_gen_config=ImageGenConfig(
                enabled=True,
                api_key="image-key-a",
                base_url="https://image-a.example.com/v1",
                model="gpt-image-a",
                proxy="http://proxy-a",
                timeout=20,
                reference_image="ref-a.png",
            ),
        )

    image_tool = loop.tools.get("image_gen")

    loop.image_gen_config = ImageGenConfig(
        enabled=True,
        api_key="image-key-b",
        base_url="https://image-b.example.com/v1",
        model="gpt-image-b",
        proxy="http://proxy-b",
        timeout=45,
        reference_image="__default__",
    )
    loop.restrict_to_workspace = True

    loop._apply_runtime_tool_config()

    assert loop.tools.get("image_gen") is image_tool
    assert image_tool is not None
    assert image_tool._api_key == "image-key-b"
    assert image_tool.base_url == "https://image-b.example.com/v1"
    assert image_tool.model == "gpt-image-b"
    assert image_tool.proxy == "http://proxy-b"
    assert image_tool.timeout == 45
    assert image_tool._default_reference == "__default__"
    assert image_tool.restrict_to_workspace is True


def test_set_tool_context_updates_message_history_and_image_tools(tmp_path) -> None:
    class _ContextRecorder:
        def __init__(self, name: str) -> None:
            self._name = name
            self.calls: list[tuple] = []
            self.personas: list[str | None] = []

        @property
        def name(self) -> str:
            return self._name

        def set_context(self, *args) -> None:
            self.calls.append(args)

        def set_persona(self, persona: str | None) -> None:
            self.personas.append(persona)

    with patch("hahobot.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=MessageBus(),
            provider=_provider(),
            workspace=tmp_path,
        )

    message_tool = _ContextRecorder("message")
    spawn_tool = _ContextRecorder("spawn")
    cron_tool = _ContextRecorder("cron")
    history_search_tool = _ContextRecorder("history_search")
    history_expand_tool = _ContextRecorder("history_expand")
    image_tool = _ContextRecorder("image_gen")

    for tool in (
        message_tool,
        spawn_tool,
        cron_tool,
        history_search_tool,
        history_expand_tool,
        image_tool,
    ):
        loop.tools.register(tool)

    loop._set_tool_context("telegram/main", "chat-1", "msg-1", "alice")

    assert message_tool.calls == [("telegram/main", "chat-1", "msg-1")]
    assert spawn_tool.calls == [("telegram/main", "chat-1")]
    assert cron_tool.calls == [("telegram/main", "chat-1")]
    assert history_search_tool.calls == [("telegram/main", "chat-1", "alice")]
    assert history_expand_tool.calls == [("telegram/main", "chat-1", "alice")]
    assert image_tool.personas == ["alice"]


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
