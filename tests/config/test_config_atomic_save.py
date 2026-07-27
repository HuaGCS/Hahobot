"""Regression tests for crash-safe config writes."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from hahobot.config.loader import load_config, save_config
from hahobot.config.schema import Config


def test_save_config_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "config.json"

    save_config(Config(), path)

    assert load_config(path).agents.defaults.model


@pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX file modes")
def test_save_config_preserves_existing_file_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o600)

    save_config(Config(), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_config_preserves_existing_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"existing": true}', encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    def fail_replace(self: Path, target: Path) -> Path:  # noqa: ARG001
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated crash"):
        save_config(Config(), path)

    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before) == {"existing": True}
    assert not list(tmp_path.glob(".config.json.*.tmp"))
