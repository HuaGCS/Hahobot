import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from hahobot.cli import commands
from hahobot.cli.commands import app
from hahobot.config.schema import Config
from hahobot.providers.base import LLMResponse, ToolCallRequest
from hahobot.session.manager import SessionManager

runner = CliRunner()


def _write_config(config_path: Path, workspace: Path) -> Path:
    config = Config.model_validate(
        {
            "providers": {"openrouter": {"apiKey": "sk-test"}},
            "agents": {
                "defaults": {
                    "workspace": str(workspace),
                    "model": "openai/gpt-4.1-mini",
                    "contextWindowTokens": 4096,
                    "maxTokens": 256,
                }
            },
        }
    )
    payload = config.model_dump(by_alias=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def _save_session(workspace: Path, key: str, *messages: tuple[str, str]) -> None:
    manager = SessionManager(workspace)
    session = manager.get_or_create(key)
    for role, content in messages:
        session.add_message(role, content)
    manager.save(session)


class _FakeCompactProvider:
    def __init__(self) -> None:
        self.generation = SimpleNamespace(max_tokens=256, temperature=0.7, reasoning_effort=None)
        self.compaction_calls = 0

    def get_default_model(self) -> str:
        return "openai/gpt-4.1-mini"

    def estimate_prompt_tokens(self, messages, _tools, _model):
        return len(messages) * 1200, "fake-counter"

    async def chat_with_retry(self, **_kwargs):
        self.compaction_calls += 1
        return LLMResponse(
            content=None,
            finish_reason="tool_calls",
            tool_calls=[
                ToolCallRequest(
                    id=f"save-memory-{self.compaction_calls}",
                    name="save_memory",
                    arguments={
                        "history_entry": f"summary {self.compaction_calls}",
                        "memory_update": "",
                    },
                )
            ],
        )


def test_sessions_compact_json_compacts_saved_session(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)
    _save_session(
        workspace,
        "cli:alpha",
        ("user", "A" * 200),
        ("assistant", "B" * 200),
        ("user", "C" * 200),
        ("assistant", "D" * 200),
        ("user", "E" * 200),
        ("assistant", "F" * 200),
    )

    provider = _FakeCompactProvider()
    monkeypatch.setattr("hahobot.cli.commands._make_provider", lambda _config: provider)

    result = runner.invoke(
        app,
        ["sessions", "compact", "cli:alpha", "--config", str(config_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["key"] == "cli:alpha"
    assert payload["status"] == "compacted"
    assert payload["last_consolidated_before"] == 0
    assert payload["last_consolidated_after"] > 0
    assert payload["archived_message_count"] > 0
    assert payload["prompt_tokens_after"] < payload["prompt_tokens_before"]
    assert provider.compaction_calls >= 1
    history_file = workspace / "memory" / "history.jsonl"
    assert history_file.exists()
    assert "summary" in history_file.read_text(encoding="utf-8")


def test_sessions_compact_missing_session_returns_error(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)
    monkeypatch.setattr("hahobot.cli.commands._make_provider", lambda _config: _FakeCompactProvider())

    result = runner.invoke(
        app,
        ["sessions", "compact", "cli:missing", "--config", str(config_path)],
    )

    assert result.exit_code == 1
    assert "Session not found: cli:missing" in result.stdout


@pytest.mark.asyncio
async def test_local_compact_command_defaults_to_current_session(tmp_path: Path) -> None:
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _save_session(
        workspace,
        "cli:alpha",
        ("user", "A" * 200),
        ("assistant", "B" * 200),
        ("user", "C" * 200),
        ("assistant", "D" * 200),
        ("user", "E" * 200),
        ("assistant", "F" * 200),
    )

    manager = SessionManager(workspace)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_FakeCompactProvider(),
        workspace=workspace,
        model="openai/gpt-4.1-mini",
        context_window_tokens=4096,
        session_manager=manager,
    )

    try:
        result = await commands._handle_local_compact_command(
            "/compact",
            loop=loop,
            current_session_id="cli:alpha",
        )
    finally:
        await loop.close_mcp()

    assert "hahobot sessions compact" in result.text
    assert "Session: cli:alpha" in result.text
    assert "Result: Compaction completed." in result.text
