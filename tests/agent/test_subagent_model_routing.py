"""Tests for the per-spawn model routing on SubagentManager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hahobot.agent.subagent import SubagentManager
from hahobot.agent.tools.spawn import SpawnTool
from hahobot.bus.queue import MessageBus


def _build_manager(
    tmp_path: Path,
    *,
    model: str = "default-model",
    roles: dict[str, str] | None = None,
) -> SubagentManager:
    provider = MagicMock()
    provider.get_default_model.return_value = model
    return SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4096,
        model=model,
        model_roles=roles or {},
    )


def test_resolve_model_returns_default_when_hint_missing(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    resolved, source = manager.resolve_model(None)
    assert resolved == "default-model"
    assert source is None


def test_resolve_model_returns_default_for_blank_hint(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    resolved, source = manager.resolve_model("   ")
    assert resolved == "default-model"
    assert source is None


def test_resolve_model_maps_role_name_to_configured_target(tmp_path: Path) -> None:
    manager = _build_manager(
        tmp_path,
        roles={"fast": "openai/gpt-4.1-mini", "strong": "anthropic/claude-opus-4-5"},
    )
    resolved, source = manager.resolve_model("fast")
    assert resolved == "openai/gpt-4.1-mini"
    assert source == "role"


def test_resolve_model_passes_through_provider_qualified_literal(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    resolved, source = manager.resolve_model("openai/o3-mini")
    assert resolved == "openai/o3-mini"
    assert source == "literal"


def test_resolve_model_role_lookup_wins_over_literal_pattern(tmp_path: Path) -> None:
    manager = _build_manager(
        tmp_path,
        roles={"openai/gpt-4.1-mini": "anthropic/claude-haiku-4-5"},
    )
    resolved, source = manager.resolve_model("openai/gpt-4.1-mini")
    assert resolved == "anthropic/claude-haiku-4-5"
    assert source == "role"


def test_resolve_model_unknown_falls_back_to_default(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    resolved, source = manager.resolve_model("nonsense-role")
    assert resolved == "default-model"
    assert source == "fallback"


def test_apply_runtime_config_replaces_role_map(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path, roles={"old": "model-a"})
    from hahobot.config.schema import ExecToolConfig

    manager.apply_runtime_config(
        workspace=tmp_path,
        model="next-default",
        brave_api_key=None,
        web_proxy=None,
        web_enabled=True,
        web_search_provider="brave",
        web_search_base_url=None,
        web_search_max_results=5,
        exec_config=ExecToolConfig(),
        restrict_to_workspace=False,
        disabled_skills=[],
        model_roles={"new": "model-b"},
    )
    assert manager.model == "next-default"
    assert manager.model_roles == {"new": "model-b"}
    resolved, source = manager.resolve_model("new")
    assert resolved == "model-b"
    assert source == "role"


@pytest.mark.asyncio
async def test_spawn_passes_resolved_model_to_background_task(tmp_path: Path) -> None:
    manager = _build_manager(
        tmp_path,
        roles={"fast": "openai/gpt-4.1-mini"},
    )

    captured: dict[str, object] = {}

    async def _fake_run_subagent(task_id, task, label, mode, origin, model=None):
        captured["task_id"] = task_id
        captured["label"] = label
        captured["mode"] = mode
        captured["model"] = model

    manager._run_subagent = _fake_run_subagent  # type: ignore[method-assign]

    await manager.spawn(task="do thing", label="probe", model="fast")
    # Drain the background task created inside spawn.
    for bg in list(manager._running_tasks.values()):
        await bg
    assert captured["model"] == "openai/gpt-4.1-mini"


@pytest.mark.asyncio
async def test_spawn_tool_forwards_model_argument(tmp_path: Path) -> None:
    manager = MagicMock(spec=SubagentManager)
    manager.spawn = AsyncMock(return_value="ok")
    tool = SpawnTool(manager)
    tool.set_context(channel="cli", chat_id="direct", session_key="cli:direct")

    await tool.execute(task="do thing", model="strong")

    manager.spawn.assert_awaited_once_with(
        task="do thing",
        label=None,
        mode="implement",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        model="strong",
    )


def test_subagent_config_round_trips_through_load_and_save(tmp_path: Path) -> None:
    from hahobot.config.loader import load_config, save_config

    config_path = tmp_path / "config.json"
    config = load_config(config_path)
    config.agents.defaults.subagent.models = {
        "fast": "openai/gpt-4.1-mini",
        "strong": "anthropic/claude-opus-4-5",
    }
    save_config(config, config_path)
    reloaded = load_config(config_path)
    assert reloaded.agents.defaults.subagent.models == {
        "fast": "openai/gpt-4.1-mini",
        "strong": "anthropic/claude-opus-4-5",
    }


def test_subagent_config_coerces_invalid_role_entries(tmp_path: Path) -> None:
    from hahobot.config.schema import SubagentConfig

    cfg = SubagentConfig.model_validate(
        {
            "models": {
                "fast": "openai/gpt-4.1-mini",
                "  ": "ignored-blank-role",
                "blank_value": "   ",
                123: "not-a-string-role",
                "strong": 42,
            }
        }
    )
    assert cfg.models == {"fast": "openai/gpt-4.1-mini"}
