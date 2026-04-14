from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hahobot.utils import self_update as self_update_mod


def _fake_completed(command: list[str], stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=stderr)


def test_perform_self_update_runs_git_uv_and_bridge_when_whatsapp_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='hahobot'\n", encoding="utf-8")
    (repo / "bridge").mkdir()
    (repo / "bridge" / "package.json").write_text("{}", encoding="utf-8")

    user_bridge = tmp_path / "user-bridge"
    user_bridge.mkdir()

    commands: list[tuple[tuple[str, ...], Path]] = []
    copied: list[tuple[Path, Path]] = []
    removed: list[Path] = []

    monkeypatch.setattr(self_update_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(self_update_mod, "get_bridge_install_dir", lambda: user_bridge)
    monkeypatch.setattr(
        self_update_mod.shutil,
        "copytree",
        lambda src, dst, ignore=None: copied.append((Path(src), Path(dst))),
    )
    monkeypatch.setattr(
        self_update_mod.shutil,
        "rmtree",
        lambda path: removed.append(Path(path)),
    )

    def fake_run(command, cwd, check, capture_output, text):
        assert check is True
        assert capture_output is True
        assert text is True
        cwd_path = Path(cwd)
        commands.append((tuple(command), cwd_path))

        suffix = tuple(command[1:])
        if suffix == ("rev-parse", "--show-toplevel"):
            return _fake_completed(command, stdout=f"{repo}\n")
        if suffix == ("rev-parse", "--abbrev-ref", "HEAD"):
            return _fake_completed(command, stdout="main\n")
        if suffix == ("status", "--porcelain"):
            return _fake_completed(command)
        if suffix == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"):
            return _fake_completed(command, stdout="origin/main\n")
        if suffix == ("pull", "--ff-only"):
            return _fake_completed(command, stdout="Already up to date.\n")
        if tuple(command[1:]) == ("sync", "--locked", "--all-extras"):
            return _fake_completed(command, stdout="synced\n")
        if tuple(command[1:]) == ("install",):
            return _fake_completed(command, stdout="installed\n")
        if tuple(command[1:]) == ("run", "build"):
            return _fake_completed(command, stdout="built\n")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(self_update_mod.subprocess, "run", fake_run)

    result = self_update_mod.perform_self_update(
        channels_config={"whatsapp": {"enabled": True}},
        language="en",
        repo_root=repo,
    )

    assert result == repo.resolve(strict=False)
    assert removed == [user_bridge]
    assert copied == [(repo / "bridge", user_bridge)]
    assert [cmd for cmd, _cwd in commands] == [
        ("/usr/bin/git", "rev-parse", "--show-toplevel"),
        ("/usr/bin/git", "rev-parse", "--abbrev-ref", "HEAD"),
        ("/usr/bin/git", "status", "--porcelain"),
        ("/usr/bin/git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"),
        ("/usr/bin/git", "pull", "--ff-only"),
        ("/usr/bin/uv", "sync", "--locked", "--all-extras"),
        ("/usr/bin/npm", "install"),
        ("/usr/bin/npm", "run", "build"),
    ]


def test_perform_self_update_bridge_only_skips_git_and_uv(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='hahobot'\n", encoding="utf-8")
    (repo / "bridge").mkdir()
    (repo / "bridge" / "package.json").write_text("{}", encoding="utf-8")

    user_bridge = tmp_path / "user-bridge"
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(self_update_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(self_update_mod, "get_bridge_install_dir", lambda: user_bridge)
    monkeypatch.setattr(self_update_mod.shutil, "copytree", lambda src, dst, ignore=None: None)
    monkeypatch.setattr(self_update_mod.shutil, "rmtree", lambda path: None)

    def fake_run(command, cwd, check, capture_output, text):
        commands.append(tuple(command))
        if tuple(command[1:]) == ("install",):
            return _fake_completed(command, stdout="installed\n")
        if tuple(command[1:]) == ("run", "build"):
            return _fake_completed(command, stdout="built\n")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(self_update_mod.subprocess, "run", fake_run)

    result = self_update_mod.perform_self_update(
        channels_config={"whatsapp": {"enabled": False}},
        language="en",
        repo_root=repo,
        bridge_only=True,
    )

    assert result == repo.resolve(strict=False)
    assert commands == [
        ("/usr/bin/npm", "install"),
        ("/usr/bin/npm", "run", "build"),
    ]


def test_perform_self_update_rejects_dirty_worktree(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='hahobot'\n", encoding="utf-8")

    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(self_update_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(command, cwd, check, capture_output, text):
        commands.append(tuple(command))
        suffix = tuple(command[1:])
        if suffix == ("rev-parse", "--show-toplevel"):
            return _fake_completed(command, stdout=f"{repo}\n")
        if suffix == ("rev-parse", "--abbrev-ref", "HEAD"):
            return _fake_completed(command, stdout="main\n")
        if suffix == ("status", "--porcelain"):
            return _fake_completed(
                command,
                stdout=" M hahobot/command/builtin.py\n?? notes.txt\n",
            )
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(self_update_mod.subprocess, "run", fake_run)

    with pytest.raises(self_update_mod.SelfUpdateError) as excinfo:
        self_update_mod.perform_self_update(
            channels_config={"whatsapp": {"enabled": False}},
            language="en",
            repo_root=repo,
        )

    message = str(excinfo.value)
    assert "working tree is not clean" in message.lower()
    assert "hahobot/command/builtin.py" in message
    assert commands == [
        ("/usr/bin/git", "rev-parse", "--show-toplevel"),
        ("/usr/bin/git", "rev-parse", "--abbrev-ref", "HEAD"),
        ("/usr/bin/git", "status", "--porcelain"),
    ]


def test_inspect_self_update_force_mode_allows_dirty_tree(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='hahobot'\n", encoding="utf-8")

    monkeypatch.setattr(self_update_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(command, cwd, check, capture_output, text):
        suffix = tuple(command[1:])
        if suffix == ("rev-parse", "--show-toplevel"):
            return _fake_completed(command, stdout=f"{repo}\n")
        if suffix == ("rev-parse", "--abbrev-ref", "HEAD"):
            return _fake_completed(command, stdout="main\n")
        if suffix == ("status", "--porcelain"):
            return _fake_completed(command, stdout=" M tracked.txt\n")
        if suffix == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"):
            return _fake_completed(command, stdout="origin/main\n")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(self_update_mod.subprocess, "run", fake_run)

    result = self_update_mod.inspect_self_update(
        channels_config={"whatsapp": {"enabled": False}},
        language="en",
        repo_root=repo,
        force=True,
    )

    assert result.mode == "force"
    assert result.ready is True
    assert result.worktree_clean is False
    assert "tracked.txt" in result.dirty_changes


def test_format_self_update_check_lists_blocking_issues() -> None:
    result = self_update_mod.SelfUpdateCheckResult(
        mode="bridge",
        project_root=Path("/tmp/hahobot"),
        repo_root=None,
        branch=None,
        upstream=None,
        worktree_clean=None,
        dirty_changes="",
        bridge_required=True,
        git_available=False,
        uv_available=None,
        npm_available=False,
        issues=("npm is missing",),
    )

    rendered = self_update_mod.format_self_update_check(result, language="en")

    assert "Update Check" in rendered
    assert "Bridge-only refresh" in rendered
    assert "blocked" in rendered
    assert "npm is missing" in rendered
