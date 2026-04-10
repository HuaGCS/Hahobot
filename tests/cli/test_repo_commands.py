import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from hahobot.cli import commands
from hahobot.cli.commands import app
from hahobot.config.schema import Config

runner = CliRunner()


def _write_config(config_path: Path, workspace: Path) -> Path:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    payload = config.model_dump(by_alias=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    _git(workspace, "init", "-b", "main")
    _git(workspace, "config", "user.name", "Test User")
    _git(workspace, "config", "user.email", "test@example.com")
    (workspace / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    _git(workspace, "commit", "-m", "init")


def test_repo_status_json_includes_counts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    config_path = _write_config(tmp_path / "config.json", workspace)

    (workspace / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (workspace / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(workspace, "add", "staged.txt")
    (workspace / "untracked.txt").write_text("extra\n", encoding="utf-8")

    result = runner.invoke(app, ["repo", "status", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["is_git_repo"] is True
    assert payload["repo_root"] == str(workspace)
    assert payload["branch"] == "main"
    assert payload["staged_count"] == 1
    assert payload["modified_count"] == 1
    assert payload["untracked_count"] == 1
    assert payload["clean"] is False


def test_repo_diff_supports_staged_name_only_json(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    config_path = _write_config(tmp_path / "config.json", workspace)

    (workspace / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (workspace / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(workspace, "add", "staged.txt")

    result = runner.invoke(
        app,
        ["repo", "diff", "--config", str(config_path), "--staged", "--name-only", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["staged"] is True
    assert payload["name_only"] is True
    assert payload["files"] == ["staged.txt"]
    assert payload["output"] == "staged.txt"


def test_repo_status_non_repo_returns_error(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)

    result = runner.invoke(app, ["repo", "status", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Git repo: no" in result.stdout


def test_local_repo_command_status_and_diff(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _init_repo(workspace)

    (workspace / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (workspace / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(workspace, "add", "staged.txt")

    status = commands._handle_local_repo_command("/repo status", workspace=workspace)
    diff = commands._handle_local_repo_command("/repo diff", workspace=workspace)
    staged = commands._handle_local_repo_command("/repo diff staged", workspace=workspace)

    assert "hahobot repo status" in status.text
    assert "Git repo: yes" in status.text
    assert "hahobot repo diff" in diff.text
    assert "tracked.txt" in diff.text
    assert "hahobot repo diff --staged" in staged.text
    assert "staged.txt" in staged.text
