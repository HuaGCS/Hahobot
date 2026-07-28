"""CLI coverage for the local-memory to shared-Mem0 backfill command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from hahobot.agent import memory_shared_backfill as backfill_module
from hahobot.cli.commands import app
from hahobot.config.schema import Config, SharedMemoryConfig

runner = CliRunner()


def _write_config(config_path: Path, workspace: Path) -> Path:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.memory.shared = SharedMemoryConfig.model_validate(
        {
            "enabled": True,
            "baseUrl": "https://mem0.invalid:8888",
            "apiKey": "cli-api-key-needle",
            "userId": "cli-shared-user",
            "personaEnabled": True,
            "globalWriteMode": "full",
        }
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config.model_dump(by_alias=True), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path


def test_cli_dry_run_is_read_only_and_redacts_candidate_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "PROFILE.md").write_text(
        "cli-memory-body-needle\n\n<private>cli-private-needle</private>",
        encoding="utf-8",
    )
    config_path = _write_config(tmp_path / "instance" / "config.json", workspace)

    async def forbidden_execute(*args, **kwargs):
        raise AssertionError("dry-run must not construct a delivery path")

    monkeypatch.setattr(
        backfill_module,
        "execute_shared_memory_backfill",
        forbidden_execute,
    )

    result = runner.invoke(
        app,
        [
            "memory",
            "shared",
            "backfill",
            "--dry-run",
            "--config",
            str(config_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "dry_run"
    assert payload["dryRun"] is True
    assert payload["totals"]["candidateWrites"] == 1
    assert "cli-memory-body-needle" not in result.stdout
    assert "cli-private-needle" not in result.stdout
    assert "cli-api-key-needle" not in result.stdout
    assert not (config_path.parent / "shared-memory").exists()
    assert list(tmp_path.rglob("shared.sqlite")) == []


def test_cli_rejects_unknown_persona_without_writing_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "personas" / "Coder").mkdir(parents=True)
    (workspace / "PROFILE.md").write_text("safe-profile", encoding="utf-8")
    config_path = _write_config(tmp_path / "instance" / "config.json", workspace)

    result = runner.invoke(
        app,
        [
            "memory",
            "shared",
            "backfill",
            "--dry-run",
            "--persona",
            "missing-persona",
            "--config",
            str(config_path),
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload == {"status": "error", "error": "unknown persona: missing-persona"}
    assert "cli-api-key-needle" not in result.stdout
    assert not (config_path.parent / "shared-memory").exists()
    assert list(tmp_path.rglob("shared.sqlite")) == []
