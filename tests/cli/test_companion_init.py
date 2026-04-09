import json
import re
from pathlib import Path

from typer.testing import CliRunner

from hahobot.cli.commands import app
from hahobot.config.schema import Config

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
    return ansi_escape.sub("", text)


def _deep_update(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _write_config(config_path: Path, workspace: Path, patch: dict | None = None) -> Path:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    payload = config.model_dump(by_alias=True)
    if patch:
        _deep_update(payload, patch)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def test_companion_init_creates_persona_scaffold_and_heartbeat_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config_path = _write_config(tmp_path / "config.json", workspace)

    result = runner.invoke(app, ["companion", "init", "--config", str(config_path), "--persona", "Aria"])

    output = _strip_ansi(result.stdout)
    persona_dir = workspace / "personas" / "Aria"
    assert result.exit_code == 0
    assert "Companion scaffold ready for persona 'Aria'" in output
    assert (persona_dir / "SOUL.md").exists()
    assert (persona_dir / "USER.md").exists()
    assert (persona_dir / "STYLE.md").exists()
    assert (persona_dir / "VOICE.json").exists()
    manifest = json.loads((persona_dir / ".hahobot" / "st_manifest.json").read_text(encoding="utf-8"))
    assert (workspace / "HEARTBEAT.md").exists()
    assert "Companion check-in" in (workspace / "HEARTBEAT.md").read_text(encoding="utf-8")
    assert set(manifest["scene_prompts"]) == {"daily", "comfort", "date"}


def test_companion_init_reference_image_is_copied_into_persona_assets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "aria.png"
    source.write_bytes(b"fake-png")
    config_path = _write_config(tmp_path / "config.json", workspace)

    result = runner.invoke(
        app,
        [
            "companion",
            "init",
            "--config",
            str(config_path),
            "--persona",
            "Aria",
            "--reference-image",
            str(source),
        ],
    )

    persona_dir = workspace / "personas" / "Aria"
    copied = persona_dir / "assets" / "aria.png"
    manifest = json.loads((persona_dir / ".hahobot" / "st_manifest.json").read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert source.exists()
    assert copied.exists()
    assert copied.read_bytes() == source.read_bytes()
    assert manifest["reference_image"] == "assets/aria.png"
    assert set(manifest["scene_prompts"]) == {"daily", "comfort", "date"}


def test_companion_init_preserves_existing_files_without_force(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    persona_dir = workspace / "personas" / "Aria"
    persona_dir.mkdir(parents=True)
    (persona_dir / "SOUL.md").write_text("custom soul", encoding="utf-8")
    config_path = _write_config(tmp_path / "config.json", workspace)

    result = runner.invoke(app, ["companion", "init", "--config", str(config_path), "--persona", "Aria"])

    output = _strip_ansi(result.stdout)
    assert result.exit_code == 0
    assert "Kept existing:" in output
    assert (persona_dir / "SOUL.md").read_text(encoding="utf-8") == "custom soul"


def test_companion_init_force_overwrites_managed_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    persona_dir = workspace / "personas" / "Aria"
    persona_dir.mkdir(parents=True)
    (persona_dir / "SOUL.md").write_text("custom soul", encoding="utf-8")
    config_path = _write_config(tmp_path / "config.json", workspace)

    result = runner.invoke(
        app,
        ["companion", "init", "--config", str(config_path), "--persona", "Aria", "--force"],
    )

    assert result.exit_code == 0
    assert "warm, grounded long-term companion" in (persona_dir / "SOUL.md").read_text(encoding="utf-8")
