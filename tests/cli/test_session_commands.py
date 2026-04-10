import json
from pathlib import Path

from typer.testing import CliRunner

from hahobot.bus.events import OutboundMessage
from hahobot.cli import commands
from hahobot.cli.commands import app
from hahobot.config.schema import Config
from hahobot.session.manager import SessionManager

runner = CliRunner()


def _write_config(config_path: Path, workspace: Path) -> Path:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    payload = config.model_dump(by_alias=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def _save_session(
    workspace: Path,
    key: str,
    *messages: tuple[str, str],
    persona: str | None = None,
) -> None:
    manager = SessionManager(workspace)
    session = manager.get_or_create(key)
    if persona:
        session.metadata["persona"] = persona
    for role, content in messages:
        session.add_message(role, content)
    manager.save(session)


def test_sessions_list_json_includes_preview_and_persona(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)
    _save_session(workspace, "cli:alpha", ("user", "hello"), ("assistant", "hi there"), persona="Aria")
    _save_session(workspace, "telegram:42", ("user", "ping from telegram"))

    result = runner.invoke(app, ["sessions", "list", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 2
    keys = [item["key"] for item in payload["sessions"]]
    assert "cli:alpha" in keys
    summary = next(item for item in payload["sessions"] if item["key"] == "cli:alpha")
    assert summary["persona"] == "Aria"
    assert summary["preview"] == "hi there"
    assert summary["last_role"] == "assistant"


def test_sessions_list_cli_only_filters_non_cli_sessions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)
    _save_session(workspace, "cli:alpha", ("user", "hello"))
    _save_session(workspace, "telegram:42", ("user", "ping from telegram"))
    _save_session(workspace, "cron:job-1", ("assistant", "background"))

    result = runner.invoke(
        app,
        ["sessions", "list", "--config", str(config_path), "--cli-only", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["sessions"][0]["key"] == "cli:alpha"


def test_sessions_show_json_returns_recent_messages_and_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)
    _save_session(
        workspace,
        "cli:alpha",
        ("user", "hello"),
        ("assistant", "working on it"),
        ("assistant", "final answer"),
        persona="Aria",
    )

    result = runner.invoke(
        app,
        ["sessions", "show", "cli:alpha", "--config", str(config_path), "--limit", "2", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["key"] == "cli:alpha"
    assert payload["persona"] == "Aria"
    assert payload["message_count"] == 3
    assert [message["content"] for message in payload["messages"]] == [
        "working on it",
        "final answer",
    ]


def test_sessions_show_missing_session_returns_error(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)

    result = runner.invoke(app, ["sessions", "show", "cli:missing", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Session not found: cli:missing" in result.stdout


def test_sessions_export_json_writes_default_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)
    _save_session(
        workspace,
        "cli:alpha",
        ("user", "hello"),
        ("assistant", "hi there"),
        persona="Aria",
    )

    result = runner.invoke(
        app,
        ["sessions", "export", "cli:alpha", "--config", str(config_path), "--format", "json"],
    )

    assert result.exit_code == 0
    export_path = workspace / "out" / "sessions" / "cli_alpha.json"
    assert export_path.exists()
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["key"] == "cli:alpha"
    assert payload["persona"] == "Aria"
    assert [message["content"] for message in payload["messages"]] == ["hello", "hi there"]
    assert str(export_path).replace("\n", "") in result.stdout.replace("\n", "")


def test_sessions_export_markdown_supports_custom_output(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)
    _save_session(
        workspace,
        "cli:alpha",
        ("user", "hello"),
        ("assistant", "hi there"),
    )
    export_path = tmp_path / "exports" / "alpha.md"

    result = runner.invoke(
        app,
        [
            "sessions",
            "export",
            "cli:alpha",
            "--config",
            str(config_path),
            "--format",
            "md",
            "--output",
            str(export_path),
        ],
    )

    assert result.exit_code == 0
    assert export_path.exists()
    content = export_path.read_text(encoding="utf-8")
    assert "# Session Export: cli:alpha" in content
    assert "```text" in content
    assert "hi there" in content


def test_sessions_export_missing_session_returns_error(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)

    result = runner.invoke(
        app,
        ["sessions", "export", "cli:missing", "--config", str(config_path)],
    )

    assert result.exit_code == 1
    assert "Session not found: cli:missing" in result.stdout


def test_agent_continue_uses_latest_cli_session(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    config = Config()
    config.agents.defaults.workspace = str(workspace)

    _save_session(workspace, "cli:older", ("user", "first"))
    _save_session(workspace, "cli:newer", ("user", "second"))
    _save_session(workspace, "telegram:42", ("user", "telegram latest"))

    seen: dict[str, object] = {}

    monkeypatch.setattr("hahobot.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("hahobot.config.loader.resolve_config_env_vars", lambda loaded: loaded)
    monkeypatch.setattr("hahobot.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("hahobot.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("hahobot.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr(
        "hahobot.cron.service.CronService",
        lambda _store, **_kwargs: object(),
    )
    monkeypatch.setattr("hahobot.cli.commands._print_agent_response", lambda *_args, **_kwargs: None)

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            seen["session_manager"] = kwargs.get("session_manager")
            self.channels_config = None

        async def process_direct(self, content, session_key, **_kwargs):
            seen["content"] = content
            seen["session_key"] = session_key
            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr("hahobot.agent.loop.AgentLoop", _FakeAgentLoop)

    result = runner.invoke(
        app,
        ["agent", "-m", "hello", "--continue", "-c", str(config_path)],
    )

    assert result.exit_code == 0
    assert seen["content"] == "hello"
    assert seen["session_key"] == "cli:newer"
    assert "Resuming session: cli:newer" in result.stdout


def test_agent_rejects_continue_and_explicit_session_together(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    config = Config()
    config.agents.defaults.workspace = str(workspace)

    monkeypatch.setattr("hahobot.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("hahobot.config.loader.resolve_config_env_vars", lambda loaded: loaded)
    monkeypatch.setattr("hahobot.cli.commands.sync_workspace_templates", lambda _path: None)

    result = runner.invoke(
        app,
        ["agent", "-m", "hello", "--continue", "--session", "cli:manual", "-c", str(config_path)],
    )

    assert result.exit_code == 1
    assert "choose only one of --session, --continue, or --pick-session" in result.stdout


def test_agent_pick_session_uses_selected_cli_session(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    config = Config()
    config.agents.defaults.workspace = str(workspace)

    _save_session(workspace, "cli:older", ("user", "first"))
    _save_session(workspace, "cli:newer", ("assistant", "second answer"), persona="Aria")
    _save_session(workspace, "telegram:42", ("user", "telegram latest"))

    seen: dict[str, object] = {}

    monkeypatch.setattr("hahobot.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("hahobot.config.loader.resolve_config_env_vars", lambda loaded: loaded)
    monkeypatch.setattr("hahobot.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("hahobot.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("hahobot.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr(
        "hahobot.cron.service.CronService",
        lambda _store, **_kwargs: object(),
    )
    monkeypatch.setattr("hahobot.cli.commands._print_agent_response", lambda *_args, **_kwargs: None)

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            self.channels_config = None

        async def process_direct(self, content, session_key, **_kwargs):
            seen["content"] = content
            seen["session_key"] = session_key
            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr("hahobot.agent.loop.AgentLoop", _FakeAgentLoop)

    result = runner.invoke(
        app,
        ["agent", "-m", "hello", "--pick-session", "-c", str(config_path)],
        input="2\n",
    )

    assert result.exit_code == 0
    assert seen["content"] == "hello"
    assert seen["session_key"] == "cli:older"
    assert "Select a session to resume" in result.stdout
    assert "Selected session: cli:older" in result.stdout


def test_agent_pick_session_conflicts_with_continue(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    config = Config()
    config.agents.defaults.workspace = str(workspace)

    monkeypatch.setattr("hahobot.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("hahobot.config.loader.resolve_config_env_vars", lambda loaded: loaded)
    monkeypatch.setattr("hahobot.cli.commands.sync_workspace_templates", lambda _path: None)

    result = runner.invoke(
        app,
        ["agent", "-m", "hello", "--continue", "--pick-session", "-c", str(config_path)],
    )

    assert result.exit_code == 1
    assert "choose only one of --session, --continue, or --pick-session" in result.stdout


def test_local_session_command_list_and_current(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _save_session(workspace, "cli:alpha", ("assistant", "hello alpha"))
    manager = SessionManager(workspace)

    current = commands._handle_local_session_command(
        "/session current",
        session_manager=manager,
        current_session_id="cli:alpha",
    )
    listing = commands._handle_local_session_command(
        "/session list",
        session_manager=manager,
        current_session_id="cli:alpha",
    )

    assert current.new_session_id is None
    assert current.text == "Current session: cli:alpha"
    assert "cli:alpha" in listing.text
    assert "hello alpha" in listing.text


def test_local_session_command_use_and_show(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _save_session(workspace, "cli:alpha", ("user", "first"), ("assistant", "second"), persona="Aria")
    manager = SessionManager(workspace)

    switched = commands._handle_local_session_command(
        "/session use alpha",
        session_manager=manager,
        current_session_id="cli:direct",
    )
    detail = commands._handle_local_session_command(
        "/session show alpha",
        session_manager=manager,
        current_session_id="cli:direct",
    )

    assert switched.new_session_id == "cli:alpha"
    assert switched.text == "Switched to session: cli:alpha"
    assert "hahobot sessions show cli:alpha" in detail.text
    assert "Persona: Aria" in detail.text
    assert "second" in detail.text


def test_local_session_command_new_and_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _save_session(workspace, "cli:alpha", ("assistant", "hello alpha"))
    manager = SessionManager(workspace)

    existing = commands._handle_local_session_command(
        "/session new alpha",
        session_manager=manager,
        current_session_id="cli:direct",
    )
    generated = commands._handle_local_session_command(
        "/session new",
        session_manager=manager,
        current_session_id="cli:direct",
    )
    missing = commands._handle_local_session_command(
        "/session use missing",
        session_manager=manager,
        current_session_id="cli:direct",
    )

    assert existing.new_session_id is None
    assert "Session already exists: cli:alpha" in existing.text
    assert generated.new_session_id is not None
    assert generated.new_session_id.startswith("cli:")
    assert "Started new session" in generated.text
    assert missing.new_session_id is None
    assert "Session not found: cli:missing" in missing.text


def test_local_session_command_export_defaults_to_current(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _save_session(workspace, "cli:alpha", ("assistant", "hello alpha"))
    manager = SessionManager(workspace)

    exported = commands._handle_local_session_command(
        "/session export",
        session_manager=manager,
        current_session_id="cli:alpha",
    )

    export_path = workspace / "out" / "sessions" / "cli_alpha.md"
    assert exported.new_session_id is None
    assert "Exported session: cli:alpha" in exported.text
    assert str(export_path) in exported.text
    assert export_path.exists()
