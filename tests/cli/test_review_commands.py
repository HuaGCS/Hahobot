import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from hahobot.cli import commands
from hahobot.cli.commands import app
from hahobot.config.schema import Config
from hahobot.providers.base import LLMResponse

runner = CliRunner()


def _write_config(config_path: Path, workspace: Path) -> Path:
    config = Config.model_validate(
        {
            "providers": {"openrouter": {"apiKey": "sk-test"}},
            "agents": {"defaults": {"workspace": str(workspace), "model": "openai/gpt-4.1-mini"}},
        }
    )
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


class _FakeReviewProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []
        self.generation = SimpleNamespace(max_tokens=4096, temperature=0.7, reasoning_effort=None)

    async def chat_with_retry(self, **kwargs):
        self.calls.append(kwargs)
        return LLMResponse(content=self.content)


def test_review_command_runs_provider_on_unstaged_diff(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    config_path = _write_config(tmp_path / "config.json", workspace)
    (workspace / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")

    provider = _FakeReviewProvider("Findings:\n- [medium] `tracked.txt`: missing regression test.")
    monkeypatch.setattr("hahobot.cli.commands._make_provider", lambda _config: provider)

    result = runner.invoke(app, ["review", "--config", str(config_path), "--no-markdown"])

    assert result.exit_code == 0
    assert "Findings:" in result.stdout
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["model"] == "openai/gpt-4.1-mini"
    user_prompt = call["messages"][1]["content"]
    assert "tracked.txt" in user_prompt
    assert "+two" in user_prompt


def test_review_command_skips_provider_when_diff_is_empty(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    config_path = _write_config(tmp_path / "config.json", workspace)

    provider = _FakeReviewProvider("should not be used")
    monkeypatch.setattr("hahobot.cli.commands._make_provider", lambda _config: provider)

    result = runner.invoke(app, ["review", "--config", str(config_path), "--no-markdown"])

    assert result.exit_code == 0
    assert "No diff to review." in result.stdout
    assert provider.calls == []


def test_review_command_non_repo_returns_error(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = _write_config(tmp_path / "config.json", workspace)

    provider = _FakeReviewProvider("unused")
    monkeypatch.setattr("hahobot.cli.commands._make_provider", lambda _config: provider)

    result = runner.invoke(app, ["review", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Error:" in result.stdout
    assert provider.calls == []


@pytest.mark.asyncio
async def test_local_review_command_supports_staged_mode(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    (workspace / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")

    provider = _FakeReviewProvider("No findings.")

    result = await commands._handle_local_review_command(
        "/review staged",
        provider=provider,
        model="openai/gpt-4.1-mini",
        workspace=workspace,
    )

    assert result.text == "No findings."
    assert len(provider.calls) == 1
    user_prompt = provider.calls[0]["messages"][1]["content"]
    assert "Review mode: staged" in user_prompt
