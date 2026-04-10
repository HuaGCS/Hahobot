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
