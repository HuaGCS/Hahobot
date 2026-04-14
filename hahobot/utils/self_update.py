"""Helpers for the /update slash command."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hahobot.config.paths import get_bridge_install_dir

_MAX_FAILURE_LINES = 12
_MAX_FAILURE_CHARS = 1200
_MAX_DIRTY_CHARS = 600


class SelfUpdateError(RuntimeError):
    """Raised when the self-update flow cannot finish safely."""


@dataclass(frozen=True)
class SelfUpdateCheckResult:
    """Dry-run inspection result for one /update mode."""

    mode: Literal["full", "force", "bridge"]
    project_root: Path | None
    repo_root: Path | None
    branch: str | None
    upstream: str | None
    worktree_clean: bool | None
    dirty_changes: str
    bridge_required: bool
    git_available: bool
    uv_available: bool | None
    npm_available: bool | None
    issues: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.issues


def find_checkout_root(start: Path | None = None) -> Path | None:
    """Find the current source checkout root by walking upward."""
    current = (start or Path(__file__)).expanduser().resolve(strict=False)
    anchor = current if current.is_dir() else current.parent
    fallback: Path | None = None

    for parent in (anchor, *anchor.parents):
        has_git = (parent / ".git").exists()
        has_pyproject = (parent / "pyproject.toml").exists()
        if has_git and has_pyproject:
            return parent
        if fallback is None and (has_git or has_pyproject):
            fallback = parent

    return fallback


def whatsapp_bridge_enabled(channels_config: Any) -> bool:
    """Return whether the current runtime config needs the WhatsApp bridge."""
    channels = _as_mapping(channels_config)
    whatsapp = _as_mapping(channels.get("whatsapp"))
    if not whatsapp:
        return False
    if bool(whatsapp.get("enabled")):
        return True

    instances = whatsapp.get("instances")
    if not isinstance(instances, list):
        return False

    for item in instances:
        if bool(_as_mapping(item).get("enabled")):
            return True
    return False


def perform_self_update(
    *,
    channels_config: Any,
    language: str | None = None,
    repo_root: Path | None = None,
    force: bool = False,
    bridge_only: bool = False,
) -> Path:
    """Synchronize the current checkout, refresh dependencies, and rebuild enabled extras."""
    lang = _resolve_language(language)
    root = (repo_root or find_checkout_root())
    if root is None:
        raise SelfUpdateError(text(lang, "update_error_not_checkout"))
    root = root.expanduser().resolve(strict=False)

    if bridge_only:
        npm_path = _require_command("npm", language=lang, missing_key="update_error_npm_missing")
        _refresh_whatsapp_bridge(root, npm_path=npm_path, language=lang)
        return root

    git_path = _require_command("git", language=lang, missing_key="update_error_git_missing")
    resolved_root = _git_repo_root(git_path, root, language=lang)
    _ensure_clean_tracking_branch(git_path, resolved_root, language=lang, force=force)
    _run_checked(
        [git_path, "pull", "--ff-only"],
        cwd=resolved_root,
        language=lang,
        error_key="update_error_git_pull_failed",
    )

    uv_path = _require_command("uv", language=lang, missing_key="update_error_uv_missing")
    _run_checked(
        [uv_path, "sync", "--locked", "--all-extras"],
        cwd=resolved_root,
        language=lang,
        error_key="update_error_uv_sync_failed",
    )

    if whatsapp_bridge_enabled(channels_config):
        npm_path = _require_command("npm", language=lang, missing_key="update_error_npm_missing")
        _refresh_whatsapp_bridge(resolved_root, npm_path=npm_path, language=lang)

    return resolved_root


def inspect_self_update(
    *,
    channels_config: Any,
    language: str | None = None,
    repo_root: Path | None = None,
    force: bool = False,
    bridge_only: bool = False,
) -> SelfUpdateCheckResult:
    """Inspect whether one /update mode can run without mutating the repo."""
    lang = _resolve_language(language)
    mode: Literal["full", "force", "bridge"] = "bridge" if bridge_only else ("force" if force else "full")
    root = (repo_root or find_checkout_root())
    project_root = root.expanduser().resolve(strict=False) if root is not None else None
    issues: list[str] = []
    branch: str | None = None
    upstream: str | None = None
    worktree_clean: bool | None = None
    dirty_changes = ""
    repo_top: Path | None = None

    bridge_required = bridge_only or whatsapp_bridge_enabled(channels_config)
    git_path = shutil.which("git")
    uv_path = None if bridge_only else shutil.which("uv")
    npm_path = shutil.which("npm") if bridge_required else None

    if project_root is None:
        issues.append(text(lang, "update_error_not_checkout"))
    elif bridge_only:
        if not npm_path:
            issues.append(text(lang, "update_error_npm_missing"))
        if not (project_root / "bridge" / "package.json").exists():
            issues.append(
                text(lang, "update_error_bridge_source_missing", path=str(project_root / "bridge"))
            )
    else:
        if not git_path:
            issues.append(text(lang, "update_error_git_missing"))
        else:
            try:
                repo_top = _git_repo_root(git_path, project_root, language=lang)
            except SelfUpdateError as exc:
                issues.append(str(exc))

        if repo_top is not None and git_path:
            try:
                branch = _git_stdout(
                    [git_path, "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=repo_top,
                    language=lang,
                    error_key="update_error_not_checkout",
                )
            except SelfUpdateError as exc:
                issues.append(str(exc))

            if branch == "HEAD":
                issues.append(text(lang, "update_error_detached_head"))
            else:
                try:
                    upstream = _git_stdout(
                        [git_path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                        cwd=repo_top,
                        language=lang,
                        error_key="update_error_missing_upstream",
                    )
                except SelfUpdateError as exc:
                    issues.append(str(exc))

            try:
                status = _git_stdout(
                    [git_path, "status", "--porcelain"],
                    cwd=repo_top,
                    language=lang,
                    error_key="update_error_not_checkout",
                )
                dirty_changes = _trim_text(status, max_lines=20, max_chars=_MAX_DIRTY_CHARS)
                worktree_clean = not bool(status.strip())
                if not worktree_clean and not force:
                    issues.append(
                        text(lang, "update_error_dirty_worktree", changes=dirty_changes)
                    )
            except SelfUpdateError as exc:
                issues.append(str(exc))

        if not uv_path:
            issues.append(text(lang, "update_error_uv_missing"))

        if bridge_required:
            if not npm_path:
                issues.append(text(lang, "update_error_npm_missing"))
            source_root = repo_top or project_root
            if source_root is not None and not (source_root / "bridge" / "package.json").exists():
                issues.append(
                    text(lang, "update_error_bridge_source_missing", path=str(source_root / "bridge"))
                )

    return SelfUpdateCheckResult(
        mode=mode,
        project_root=project_root,
        repo_root=repo_top,
        branch=branch,
        upstream=upstream,
        worktree_clean=worktree_clean,
        dirty_changes=dirty_changes,
        bridge_required=bridge_required,
        git_available=bool(git_path),
        uv_available=(None if bridge_only else bool(uv_path)),
        npm_available=(bool(npm_path) if bridge_required else None),
        issues=tuple(issues),
    )


def format_self_update_check(result: SelfUpdateCheckResult, *, language: str | None = None) -> str:
    """Render one /update dry-run report."""
    lang = _resolve_language(language)
    lines = [
        text(lang, "update_check_title"),
        "",
        f"{text(lang, 'update_check_mode_label')}: {text(lang, _mode_text_key(result.mode))}",
        f"{text(lang, 'update_check_root_label')}: {result.repo_root or result.project_root or '-'}",
        f"{text(lang, 'update_check_branch_label')}: {result.branch or '-'}",
        f"{text(lang, 'update_check_upstream_label')}: {result.upstream or '-'}",
        f"{text(lang, 'update_check_worktree_label')}: {_worktree_status_text(result, lang)}",
        (
            f"{text(lang, 'update_check_bridge_label')}: "
            f"{text(lang, 'update_check_required') if result.bridge_required else text(lang, 'update_check_not_required')}"
        ),
        (
            f"{text(lang, 'update_check_commands_label')}: "
            f"git={_status_word(result.git_available, required=(result.mode != 'bridge'), language=lang)}, "
            f"uv={_status_word(result.uv_available, required=(result.mode != 'bridge'), language=lang)}, "
            f"npm={_status_word(result.npm_available, required=result.bridge_required, language=lang)}"
        ),
        f"{text(lang, 'update_check_result_label')}: "
        f"{text(lang, 'update_check_ready') if result.ready else text(lang, 'update_check_blocked')}",
        "",
        f"{text(lang, 'update_check_steps_label')}:",
    ]
    for step in _planned_step_texts(result, lang):
        lines.append(f"- {step}")
    if result.dirty_changes and result.worktree_clean is False:
        lines.extend([
            "",
            f"{text(lang, 'update_check_dirty_label')}:",
            result.dirty_changes,
        ])
    if result.issues:
        lines.extend(["", f"{text(lang, 'update_check_issues_label')}:"])
        for issue in result.issues:
            lines.append(f"- {issue}")
    return "\n".join(lines)


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python", by_alias=False)
        if isinstance(dumped, dict):
            return dumped
    return {}


def _require_command(name: str, *, language: str, missing_key: str) -> str:
    path = shutil.which(name)
    if path:
        return path
    raise SelfUpdateError(text(language, missing_key))


def _git_repo_root(git_path: str, root: Path, *, language: str) -> Path:
    result = _run_checked(
        [git_path, "rev-parse", "--show-toplevel"],
        cwd=root,
        language=language,
        error_key="update_error_not_checkout",
    )
    resolved = result.stdout.strip()
    if not resolved:
        raise SelfUpdateError(text(language, "update_error_not_checkout"))
    return Path(resolved).expanduser().resolve(strict=False)


def _ensure_clean_tracking_branch(git_path: str, root: Path, *, language: str, force: bool = False) -> None:
    branch = _git_stdout(
        [git_path, "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        language=language,
        error_key="update_error_not_checkout",
    )
    if branch == "HEAD":
        raise SelfUpdateError(text(language, "update_error_detached_head"))

    status = _git_stdout(
        [git_path, "status", "--porcelain"],
        cwd=root,
        language=language,
        error_key="update_error_not_checkout",
    )
    if status.strip() and not force:
        raise SelfUpdateError(
            text(
                language,
                "update_error_dirty_worktree",
                changes=_trim_text(status, max_lines=20, max_chars=_MAX_DIRTY_CHARS),
            )
        )

    _git_stdout(
        [git_path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=root,
        language=language,
        error_key="update_error_missing_upstream",
    )


def _refresh_whatsapp_bridge(repo_root: Path, *, npm_path: str, language: str) -> None:
    source = repo_root / "bridge"
    if not (source / "package.json").exists():
        raise SelfUpdateError(
            text(language, "update_error_bridge_source_missing", path=str(source))
        )

    target = get_bridge_install_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("node_modules", "dist"))

    _run_checked(
        [npm_path, "install"],
        cwd=target,
        language=language,
        error_key="update_error_bridge_install_failed",
    )
    _run_checked(
        [npm_path, "run", "build"],
        cwd=target,
        language=language,
        error_key="update_error_bridge_build_failed",
    )


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    language: str,
    error_key: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        message = text(language, error_key)
        details = _format_process_failure(exc)
        if details:
            message = f"{message}\n\n{details}"
        raise SelfUpdateError(message) from exc


def _git_stdout(
    command: list[str],
    *,
    cwd: Path,
    language: str,
    error_key: str,
) -> str:
    return _run_checked(command, cwd=cwd, language=language, error_key=error_key).stdout.strip()


def _format_process_failure(exc: subprocess.CalledProcessError) -> str:
    parts: list[str] = []
    stderr = _trim_text(exc.stderr or "")
    stdout = _trim_text(exc.stdout or "")
    if stderr:
        parts.append(stderr)
    if stdout and stdout not in parts:
        parts.append(stdout)
    return "\n".join(parts)


def _trim_text(raw: str, *, max_lines: int = _MAX_FAILURE_LINES, max_chars: int = _MAX_FAILURE_CHARS) -> str:
    cleaned = raw.strip()
    if not cleaned:
        return ""

    lines = cleaned.splitlines()
    if len(lines) > max_lines:
        cleaned = "\n".join(lines[:max_lines]) + "\n..."
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "\n..."
    return cleaned


def _resolve_language(value: str | None) -> str:
    from hahobot.agent.i18n import resolve_language

    return resolve_language(value)


def text(language: str, key: str, **kwargs: Any) -> str:
    from hahobot.agent.i18n import text as i18n_text

    return i18n_text(language, key, **kwargs)


def _mode_text_key(mode: Literal["full", "force", "bridge"]) -> str:
    if mode == "force":
        return "update_check_mode_force"
    if mode == "bridge":
        return "update_check_mode_bridge"
    return "update_check_mode_full"


def _planned_step_texts(result: SelfUpdateCheckResult, language: str) -> tuple[str, ...]:
    if result.mode == "bridge":
        return (
            text(language, "update_step_bridge_refresh"),
            text(language, "update_step_restart"),
        )
    steps: list[str] = [
        text(language, "update_step_git_pull"),
        text(language, "update_step_uv_sync"),
    ]
    if result.bridge_required:
        steps.append(text(language, "update_step_bridge_refresh"))
    steps.append(text(language, "update_step_restart"))
    return tuple(steps)


def _status_word(value: bool | None, *, required: bool, language: str) -> str:
    if not required or value is None:
        return text(language, "update_check_not_required")
    return text(language, "update_check_yes") if value else text(language, "update_check_no")


def _worktree_status_text(result: SelfUpdateCheckResult, language: str) -> str:
    if result.mode == "bridge":
        return text(language, "update_check_not_required")
    if result.worktree_clean is True:
        return text(language, "update_check_clean")
    if result.worktree_clean is False and result.mode == "force":
        return text(language, "update_check_dirty_force")
    if result.worktree_clean is False:
        return text(language, "update_check_dirty")
    return "-"
