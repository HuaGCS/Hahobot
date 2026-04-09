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


def _write_config(config_path: Path, workspace: Path, patch: dict | None = None) -> Path:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    payload = config.model_dump(by_alias=True)
    if patch:
        _deep_update(payload, patch)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def _deep_update(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _make_persona(workspace: Path, name: str = "Aria") -> Path:
    persona_dir = workspace / "personas" / name
    (persona_dir / ".hahobot").mkdir(parents=True, exist_ok=True)
    (persona_dir / "SOUL.md").write_text("You are Aria.", encoding="utf-8")
    return persona_dir


def test_companion_doctor_reports_missing_persona(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_persona(workspace, "Aria")
    config_path = _write_config(tmp_path / "config.json", workspace)

    result = runner.invoke(app, ["companion", "doctor", "--config", str(config_path), "--persona", "Missing"])

    output = _strip_ansi(result.stdout)
    assert result.exit_code == 0
    assert "Overall: FAIL" in output
    assert "Requested persona not found: Missing." in output


def test_companion_doctor_reports_empty_heartbeat_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_persona(workspace)
    (workspace / "HEARTBEAT.md").write_text("", encoding="utf-8")
    config_path = _write_config(tmp_path / "config.json", workspace)

    result = runner.invoke(app, ["companion", "doctor", "--config", str(config_path), "--persona", "Aria"])

    output = _strip_ansi(result.stdout)
    assert result.exit_code == 0
    assert "[WARN] heartbeat_file: HEARTBEAT.md is empty." in output


def test_companion_doctor_reports_voice_reply_disabled_as_warn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_persona(workspace)
    config_path = _write_config(
        tmp_path / "config.json",
        workspace,
        patch={
            "channels": {
                "voiceReply": {
                    "enabled": False,
                }
            }
        },
    )

    result = runner.invoke(app, ["companion", "doctor", "--config", str(config_path), "--persona", "Aria", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    voice_check = next(check for check in payload["checks"] if check["id"] == "voice_reply")
    assert voice_check["status"] == "warn"


def test_companion_doctor_reports_reference_image_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    persona_dir = _make_persona(workspace)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not-a-real-png")
    (persona_dir / ".hahobot" / "st_manifest.json").write_text(
        json.dumps({"reference_image": str(outside)}),
        encoding="utf-8",
    )
    config_path = _write_config(
        tmp_path / "config.json",
        workspace,
        patch={
            "tools": {
                "imageGen": {"enabled": True},
                "restrictToWorkspace": True,
            }
        },
    )

    result = runner.invoke(app, ["companion", "doctor", "--config", str(config_path), "--persona", "Aria", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    ref_check = next(check for check in payload["checks"] if check["id"] == "reference_images")
    assert ref_check["status"] == "fail"
    assert "outside the workspace" in ref_check["summary"]


def test_companion_doctor_reports_scene_shortcuts_ready(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    persona_dir = _make_persona(workspace)
    avatar = persona_dir / "avatar.png"
    avatar.write_bytes(b"png")
    (persona_dir / ".hahobot" / "st_manifest.json").write_text(
        json.dumps(
            {
                "reference_image": "avatar.png",
                "scene_prompts": {"comfort": "quiet sofa corner"},
            }
        ),
        encoding="utf-8",
    )
    config_path = _write_config(
        tmp_path / "config.json",
        workspace,
        patch={"tools": {"imageGen": {"enabled": True}}},
    )

    result = runner.invoke(app, ["companion", "doctor", "--config", str(config_path), "--persona", "Aria", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    scene_check = next(check for check in payload["checks"] if check["id"] == "scene_shortcuts")
    assert scene_check["status"] == "ok"
    assert "prompt overrides: comfort" in scene_check["detail"]


def test_companion_doctor_json_output_is_stable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_persona(workspace)
    config_path = _write_config(tmp_path / "config.json", workspace)

    result = runner.invoke(app, ["companion", "doctor", "--config", str(config_path), "--persona", "Aria", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "config_path",
        "workspace",
        "requested_persona",
        "persona",
        "overall_status",
        "ok_count",
        "warn_count",
        "fail_count",
        "checks",
    }
    assert payload["persona"] == "Aria"
    assert isinstance(payload["checks"], list)


def test_companion_doctor_success_for_minimal_companion_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    persona_dir = _make_persona(workspace)
    avatar = persona_dir / "avatar.png"
    avatar.write_bytes(b"png")
    (persona_dir / ".hahobot" / "st_manifest.json").write_text(
        json.dumps({"reference_image": "avatar.png"}),
        encoding="utf-8",
    )
    (workspace / "HEARTBEAT.md").write_text("- [ ] check in after work", encoding="utf-8")
    config_path = _write_config(
        tmp_path / "config.json",
        workspace,
        patch={
            "channels": {
                "telegram": {"enabled": True, "token": "123:abc"},
                "voiceReply": {
                    "enabled": True,
                    "provider": "edge",
                    "channels": ["telegram"],
                },
            },
            "tools": {
                "imageGen": {"enabled": True},
            },
        },
    )

    result = runner.invoke(app, ["companion", "doctor", "--config", str(config_path), "--persona", "Aria", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["overall_status"] == "ok"
    assert payload["fail_count"] == 0
    assert payload["warn_count"] == 0
