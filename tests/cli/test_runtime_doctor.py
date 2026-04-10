import json
from pathlib import Path

from typer.testing import CliRunner

from hahobot.cli.commands import app
from hahobot.config.schema import Config

runner = CliRunner()


def _deep_update(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _write_bootstrap_workspace(workspace: Path) -> None:
    (workspace / "memory").mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text("workspace rules", encoding="utf-8")
    (workspace / "SOUL.md").write_text("persona", encoding="utf-8")
    (workspace / "USER.md").write_text("user", encoding="utf-8")
    (workspace / "memory" / "MEMORY.md").write_text("memory", encoding="utf-8")


def _write_config(config_path: Path, workspace: Path, patch: dict | None = None) -> Path:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    payload = config.model_dump(by_alias=True)
    if patch:
        _deep_update(payload, patch)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def _check(payload: dict, check_id: str) -> dict:
    return next(item for item in payload["checks"] if item["id"] == check_id)


def test_doctor_reports_missing_model_credentials_and_no_channels(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_bootstrap_workspace(workspace)
    config_path = _write_config(
        tmp_path / "config.json",
        workspace,
        patch={
            "agents": {
                "defaults": {
                    "provider": "openrouter",
                    "model": "openai/gpt-4.1-mini",
                }
            },
            "tools": {
                "web": {
                    "enable": False,
                }
            },
        },
    )

    result = runner.invoke(app, ["doctor", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["overall_status"] == "fail"
    assert _check(payload, "model_route")["status"] == "fail"
    assert _check(payload, "channels")["status"] == "warn"
    assert _check(payload, "workspace_bootstrap")["status"] == "ok"

    tools_result = runner.invoke(app, ["tools", "--config", str(config_path), "--json"])

    assert tools_result.exit_code == 0
    tools_payload = json.loads(tools_result.stdout)
    assert tools_payload["web"]["enabled"] is False
    assert tools_payload["web"]["status"] == "ok"


def test_model_command_reports_provider_pool_targets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_bootstrap_workspace(workspace)
    config_path = _write_config(
        tmp_path / "config.json",
        workspace,
        patch={
            "providers": {
                "openrouter": {
                    "apiKey": "sk-or-v1-test",
                }
            },
            "agents": {
                "defaults": {
                    "providerPool": {
                        "strategy": "failover",
                        "targets": [
                            {
                                "provider": "openrouter",
                                "model": "openai/gpt-4.1-mini",
                            },
                            {
                                "provider": "ollama",
                                "model": "llama3.2",
                            },
                        ],
                    }
                }
            },
        },
    )

    result = runner.invoke(app, ["model", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["route_mode"] == "provider_pool"
    assert payload["selection_mode"] == "provider_pool"
    assert payload["provider_pool_strategy"] == "failover"
    assert payload["status"] == "ok"
    assert [target["provider"] for target in payload["targets"]] == ["openrouter", "ollama"]
    assert all(target["ready"] for target in payload["targets"])


def test_tools_command_reports_warnings_for_partial_setup(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_bootstrap_workspace(workspace)
    config_path = _write_config(
        tmp_path / "config.json",
        workspace,
        patch={
            "tools": {
                "imageGen": {
                    "enabled": True,
                },
                "mcpServers": {
                    "broken": {
                        "type": "stdio",
                        "command": "definitely-not-installed-hahobot-mcp",
                    }
                },
            }
        },
    )

    result = runner.invoke(app, ["tools", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["overall_status"] == "warn"
    assert payload["web"]["status"] == "warn"
    assert payload["image_gen"]["status"] == "warn"
    assert payload["mcp"]["status"] == "warn"
    assert payload["mcp"]["server_count"] == 1
    assert "Command not found in PATH" in payload["mcp"]["servers"][0]["issues"][0]
