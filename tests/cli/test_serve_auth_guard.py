"""Wildcard-bind auth guard for `hahobot serve`.

The OpenAI-compatible API server must refuse to start on a wildcard bind
(`0.0.0.0` / `::`) unless `api.authKey` is set, so a wildcard bind can never be
left unauthenticated. Ported from nanobot's API auth cluster (`ed483253`).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from hahobot.cli.commands import app
from hahobot.config.schema import Config

runner = CliRunner()


def _write_config(config_path: Path, workspace: Path, *, host: str, auth_key: str = "") -> Path:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.api.host = host
    config.api.auth_key = auth_key
    payload = config.model_dump(by_alias=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def _invoke_serve(config_path: Path):
    """Invoke `serve` with the heavy runtime/server pieces stubbed out."""
    with (
        patch("hahobot.cli.commands.runtime._make_provider", return_value=MagicMock()),
        patch("hahobot.agent.loop.AgentLoop", return_value=MagicMock()),
        patch("hahobot.api.server.create_app", return_value=MagicMock()) as create_app,
        patch("aiohttp.web.run_app") as run_app,
    ):
        result = runner.invoke(app, ["serve", "--config", str(config_path)])
    return result, create_app, run_app


def test_wildcard_bind_without_auth_key_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config_path = _write_config(tmp_path / "config.json", workspace, host="0.0.0.0")
    result, create_app, run_app = _invoke_serve(config_path)
    assert result.exit_code == 1
    # Guard fires before the server is created or run.
    create_app.assert_not_called()
    run_app.assert_not_called()


def test_ipv6_wildcard_bind_without_auth_key_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config_path = _write_config(tmp_path / "config.json", workspace, host="::")
    result, _, run_app = _invoke_serve(config_path)
    assert result.exit_code == 1
    run_app.assert_not_called()


def test_wildcard_bind_with_auth_key_starts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config_path = _write_config(
        tmp_path / "config.json", workspace, host="0.0.0.0", auth_key="s3cret"
    )
    result, create_app, run_app = _invoke_serve(config_path)
    assert result.exit_code == 0
    create_app.assert_called_once()
    # auth_key is forwarded into the app.
    assert create_app.call_args.kwargs.get("auth_key") == "s3cret"
    run_app.assert_called_once()


def test_loopback_bind_needs_no_auth_key(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config_path = _write_config(tmp_path / "config.json", workspace, host="127.0.0.1")
    result, create_app, run_app = _invoke_serve(config_path)
    assert result.exit_code == 0
    create_app.assert_called_once()
    run_app.assert_called_once()
