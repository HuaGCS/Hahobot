"""Tests for ``hahobot config set/get/unset`` (per-skill config in skills.json)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from hahobot.cli.commands import app
from hahobot.config.schema import Config

runner = CliRunner()


def _write_config(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = Config.model_validate(
        {
            "providers": {"openrouter": {"apiKey": "sk-test"}},
            "agents": {"defaults": {"workspace": str(workspace)}},
        }
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(config.model_dump(by_alias=True), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path, workspace


def _run(config_path: Path, *args: str):
    return runner.invoke(app, ["config", *args, "--config", str(config_path)])


def test_config_set_creates_nested_entry(tmp_path: Path) -> None:
    config_path, workspace = _write_config(tmp_path)

    result = _run(
        config_path,
        "set",
        "skills.entries.today-task.config.authCode",
        "OoPs0gbbd5BH",
    )
    assert result.exit_code == 0, result.output

    data = json.loads((workspace / "skills.json").read_text(encoding="utf-8"))
    assert data == {"entries": {"today-task": {"config": {"authCode": "OoPs0gbbd5BH"}}}}


def test_config_set_updates_without_clobbering_siblings(tmp_path: Path) -> None:
    config_path, workspace = _write_config(tmp_path)
    skills_file = workspace / "skills.json"
    skills_file.write_text(
        json.dumps(
            {"entries": {"today-task": {"enabled": True, "config": {"authCode": "old"}}}},
        ),
        encoding="utf-8",
    )

    result = _run(config_path, "set", "skills.entries.today-task.config.authCode", "new")
    assert result.exit_code == 0, result.output

    data = json.loads(skills_file.read_text(encoding="utf-8"))
    # sibling "enabled" is preserved, value is updated
    assert data["entries"]["today-task"] == {"enabled": True, "config": {"authCode": "new"}}


def test_config_set_value_defaults_to_string(tmp_path: Path) -> None:
    config_path, workspace = _write_config(tmp_path)

    # A numeric-looking authCode must stay a string (no leading-zero / type loss).
    _run(config_path, "set", "skills.entries.today-task.config.authCode", "0071")
    data = json.loads((workspace / "skills.json").read_text(encoding="utf-8"))
    assert data["entries"]["today-task"]["config"]["authCode"] == "0071"


def test_config_set_json_flag_parses_value(tmp_path: Path) -> None:
    config_path, workspace = _write_config(tmp_path)

    _run(config_path, "set", "skills.entries.today-task.enabled", "false", "--json")
    data = json.loads((workspace / "skills.json").read_text(encoding="utf-8"))
    assert data["entries"]["today-task"]["enabled"] is False


def test_config_get_scalar_and_object(tmp_path: Path) -> None:
    config_path, _ = _write_config(tmp_path)
    _run(config_path, "set", "skills.entries.today-task.config.authCode", "abc123")

    scalar = _run(config_path, "get", "skills.entries.today-task.config.authCode")
    assert scalar.exit_code == 0
    assert scalar.output.strip() == "abc123"

    obj = _run(config_path, "get", "skills.entries.today-task.config")
    assert obj.exit_code == 0
    assert json.loads(obj.output) == {"authCode": "abc123"}


def test_config_get_missing_exits_nonzero(tmp_path: Path) -> None:
    config_path, _ = _write_config(tmp_path)
    result = _run(config_path, "get", "skills.entries.nope.config.authCode")
    assert result.exit_code == 1


def test_config_unset_removes_key(tmp_path: Path) -> None:
    config_path, workspace = _write_config(tmp_path)
    _run(config_path, "set", "skills.entries.today-task.config.authCode", "abc123")

    result = _run(config_path, "unset", "skills.entries.today-task.config.authCode")
    assert result.exit_code == 0, result.output

    data = json.loads((workspace / "skills.json").read_text(encoding="utf-8"))
    assert data["entries"]["today-task"]["config"] == {}


def test_config_rejects_non_skills_namespace(tmp_path: Path) -> None:
    config_path, _ = _write_config(tmp_path)
    result = _run(config_path, "set", "agents.defaults.model", "gpt")
    assert result.exit_code == 1
    assert "skills" in result.output


def test_openclaw_alias_runs_the_same_app_and_config_command(tmp_path: Path) -> None:
    # `openclaw config set ...` must behave exactly like `hahobot config set ...`.
    from hahobot.cli import openclaw

    assert openclaw.app is app

    config_path, workspace = _write_config(tmp_path)
    result = runner.invoke(
        openclaw.app,
        [
            "config",
            "set",
            "skills.entries.today-task.config.authCode",
            "OoPs0gbbd5BH",
            "--config",
            str(config_path),
        ],
        prog_name="openclaw",
    )
    assert result.exit_code == 0, result.output
    data = json.loads((workspace / "skills.json").read_text(encoding="utf-8"))
    assert data["entries"]["today-task"]["config"]["authCode"] == "OoPs0gbbd5BH"
