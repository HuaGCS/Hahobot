from __future__ import annotations

import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path


def test_editable_lock_version_matches_pyproject() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    project_version = tomllib.loads(
        (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    locked = tomllib.loads((repo_root / "uv.lock").read_text(encoding="utf-8"))
    root_package = next(
        package
        for package in locked["package"]
        if package["name"] == "hahobot-ai" and package.get("source", {}).get("editable") == "."
    )

    assert root_package["version"] == project_version


def test_source_checkout_import_prefers_pyproject_over_stale_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    expected = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    script = textwrap.dedent(
        f"""
        import sys
        import types
        import importlib.metadata

        sys.path.insert(0, {str(repo_root)!r})
        importlib.metadata.version = lambda _distribution: "0.0.1"
        fake = types.ModuleType("hahobot.hahobot")
        fake.Hahobot = object
        fake.RunResult = object
        sys.modules["hahobot.hahobot"] = fake

        import hahobot

        print(hahobot.__version__)
        """
    )

    proc = subprocess.run(
        [sys.executable, "-S", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected
