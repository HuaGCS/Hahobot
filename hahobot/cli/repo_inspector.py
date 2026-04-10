"""Helpers for read-only local Git repository inspection."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CONFLICT_STATUSES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


@dataclass(frozen=True)
class RepoStatusSummary:
    """Serializable Git status snapshot for one workspace."""

    workspace: Path
    workspace_exists: bool
    is_git_repo: bool
    repo_root: Path | None
    branch: str | None
    detached: bool
    head: str | None
    upstream: str | None
    ahead: int
    behind: int
    staged_count: int
    modified_count: int
    untracked_count: int
    conflicted_count: int
    clean: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "workspace_exists": self.workspace_exists,
            "is_git_repo": self.is_git_repo,
            "repo_root": str(self.repo_root) if self.repo_root else None,
            "branch": self.branch,
            "detached": self.detached,
            "head": self.head,
            "upstream": self.upstream,
            "ahead": self.ahead,
            "behind": self.behind,
            "staged_count": self.staged_count,
            "modified_count": self.modified_count,
            "untracked_count": self.untracked_count,
            "conflicted_count": self.conflicted_count,
            "clean": self.clean,
            "error": self.error,
        }


@dataclass(frozen=True)
class RepoDiffSummary:
    """Serializable diff/stat view for one workspace repository."""

    workspace: Path
    workspace_exists: bool
    is_git_repo: bool
    repo_root: Path | None
    branch: str | None
    detached: bool
    head: str | None
    staged: bool
    name_only: bool
    clean: bool
    file_count: int
    files: tuple[str, ...]
    output: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "workspace_exists": self.workspace_exists,
            "is_git_repo": self.is_git_repo,
            "repo_root": str(self.repo_root) if self.repo_root else None,
            "branch": self.branch,
            "detached": self.detached,
            "head": self.head,
            "staged": self.staged,
            "name_only": self.name_only,
            "clean": self.clean,
            "file_count": self.file_count,
            "files": list(self.files),
            "output": self.output,
            "error": self.error,
        }


def _normalize_workspace(workspace: Path) -> Path:
    return workspace.expanduser().resolve()


def _workspace_error_summary(workspace: Path, error: str) -> RepoStatusSummary:
    workspace_exists = workspace.exists()
    return RepoStatusSummary(
        workspace=workspace,
        workspace_exists=workspace_exists,
        is_git_repo=False,
        repo_root=None,
        branch=None,
        detached=False,
        head=None,
        upstream=None,
        ahead=0,
        behind=0,
        staged_count=0,
        modified_count=0,
        untracked_count=0,
        conflicted_count=0,
        clean=True,
        error=error,
    )


def _run_git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None


def _normalize_git_error(result: subprocess.CompletedProcess[str] | None) -> str:
    if result is None:
        return "git executable not found."
    return (result.stderr or result.stdout or "git command failed").strip()


def _parse_tracking_line(branch_line: str) -> tuple[str | None, int, int]:
    if not branch_line.startswith("## "):
        return None, 0, 0
    body = branch_line[3:]
    branch_info, separator, tracking = body.partition(" [")
    upstream = None
    if "..." in branch_info:
        _, upstream = branch_info.split("...", 1)
    ahead = 0
    behind = 0
    if separator and tracking.endswith("]"):
        details = tracking[:-1]
        ahead_match = re.search(r"\bahead (\d+)\b", details)
        behind_match = re.search(r"\bbehind (\d+)\b", details)
        if ahead_match:
            ahead = int(ahead_match.group(1))
        if behind_match:
            behind = int(behind_match.group(1))
    return upstream, ahead, behind


def inspect_repo_status(workspace: Path) -> RepoStatusSummary:
    """Inspect read-only Git status for the given workspace path."""
    workspace = _normalize_workspace(workspace)
    if not workspace.exists():
        return _workspace_error_summary(workspace, "Workspace does not exist.")
    if not workspace.is_dir():
        return _workspace_error_summary(workspace, "Workspace is not a directory.")

    root_result = _run_git(workspace, "rev-parse", "--show-toplevel")
    if root_result is None:
        return _workspace_error_summary(workspace, "git executable not found.")
    if root_result.returncode != 0:
        return _workspace_error_summary(workspace, _normalize_git_error(root_result))

    repo_root = Path(root_result.stdout.strip())

    branch_result = _run_git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = None
    detached = False
    head = None
    if branch_result is None:
        return _workspace_error_summary(workspace, "git executable not found.")
    if branch_result.returncode == 0:
        branch = branch_result.stdout.strip() or None
    else:
        detached = True
        head_result = _run_git(workspace, "rev-parse", "--short", "HEAD")
        if head_result is not None and head_result.returncode == 0:
            head = head_result.stdout.strip() or None

    status_result = _run_git(workspace, "status", "--porcelain=1", "--branch")
    if status_result is None:
        return _workspace_error_summary(workspace, "git executable not found.")
    if status_result.returncode != 0:
        return _workspace_error_summary(workspace, _normalize_git_error(status_result))

    lines = status_result.stdout.splitlines()
    branch_line = lines[0] if lines else ""
    upstream, ahead, behind = _parse_tracking_line(branch_line)
    if branch is None and branch_line.startswith("## "):
        branch_info = branch_line[3:].partition(" [")[0]
        branch = branch_info.split("...", 1)[0] or None

    staged_count = 0
    modified_count = 0
    untracked_count = 0
    conflicted_count = 0
    change_lines = [line for line in lines[1:] if line]
    for line in change_lines:
        code = line[:2]
        if code == "??":
            untracked_count += 1
            continue
        if code == "!!":
            continue
        index_status = code[0]
        worktree_status = code[1]
        if index_status != " ":
            staged_count += 1
        if worktree_status != " ":
            modified_count += 1
        if code in _CONFLICT_STATUSES or "U" in code:
            conflicted_count += 1

    return RepoStatusSummary(
        workspace=workspace,
        workspace_exists=True,
        is_git_repo=True,
        repo_root=repo_root,
        branch=branch,
        detached=detached,
        head=head,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        staged_count=staged_count,
        modified_count=modified_count,
        untracked_count=untracked_count,
        conflicted_count=conflicted_count,
        clean=not change_lines,
        error=None,
    )


def inspect_repo_diff(
    workspace: Path,
    *,
    staged: bool = False,
    name_only: bool = False,
) -> RepoDiffSummary:
    """Inspect tracked Git diff/stat output for the given workspace path."""
    status = inspect_repo_status(workspace)
    if not status.is_git_repo:
        return RepoDiffSummary(
            workspace=status.workspace,
            workspace_exists=status.workspace_exists,
            is_git_repo=False,
            repo_root=None,
            branch=None,
            detached=False,
            head=None,
            staged=staged,
            name_only=name_only,
            clean=True,
            file_count=0,
            files=(),
            output="",
            error=status.error,
        )

    diff_args = ["diff", "--no-ext-diff"]
    if staged:
        diff_args.append("--cached")
    diff_args.append("--name-only" if name_only else "--stat")
    diff_result = _run_git(status.workspace, *diff_args)
    if diff_result is None or diff_result.returncode != 0:
        return RepoDiffSummary(
            workspace=status.workspace,
            workspace_exists=status.workspace_exists,
            is_git_repo=status.is_git_repo,
            repo_root=status.repo_root,
            branch=status.branch,
            detached=status.detached,
            head=status.head,
            staged=staged,
            name_only=name_only,
            clean=True,
            file_count=0,
            files=(),
            output="",
            error=_normalize_git_error(diff_result),
        )

    file_args = ["diff", "--no-ext-diff"]
    if staged:
        file_args.append("--cached")
    file_args.append("--name-only")
    file_result = _run_git(status.workspace, *file_args)
    if file_result is None or file_result.returncode != 0:
        return RepoDiffSummary(
            workspace=status.workspace,
            workspace_exists=status.workspace_exists,
            is_git_repo=status.is_git_repo,
            repo_root=status.repo_root,
            branch=status.branch,
            detached=status.detached,
            head=status.head,
            staged=staged,
            name_only=name_only,
            clean=True,
            file_count=0,
            files=(),
            output="",
            error=_normalize_git_error(file_result),
        )

    files = tuple(line.strip() for line in file_result.stdout.splitlines() if line.strip())
    clean = not files
    output = diff_result.stdout.strip()
    if name_only:
        output = "\n".join(files)
    if not output:
        output = "No staged tracked changes." if staged else "No unstaged tracked changes."

    return RepoDiffSummary(
        workspace=status.workspace,
        workspace_exists=status.workspace_exists,
        is_git_repo=status.is_git_repo,
        repo_root=status.repo_root,
        branch=status.branch,
        detached=status.detached,
        head=status.head,
        staged=staged,
        name_only=name_only,
        clean=clean,
        file_count=len(files),
        files=files,
        output=output,
        error=None,
    )


def render_repo_status_text(summary: RepoStatusSummary) -> str:
    """Render a compact human-readable repository status summary."""
    lines = [
        "hahobot repo status",
        "",
        f"Workspace: {summary.workspace}",
        f"Exists: {'yes' if summary.workspace_exists else 'no'}",
    ]
    if not summary.is_git_repo:
        lines.append("Git repo: no")
        if summary.error:
            lines.append(f"Error: {summary.error}")
        return "\n".join(lines)

    branch_label = (
        f"detached at {summary.head or 'unknown'}"
        if summary.detached
        else (summary.branch or "unknown")
    )
    tracking = f"{summary.ahead} ahead, {summary.behind} behind"
    lines.extend(
        [
            "Git repo: yes",
            f"Root: {summary.repo_root}",
            f"Branch: {branch_label}",
            f"Upstream: {summary.upstream or 'none'}",
            f"Tracking: {tracking}",
            f"Clean: {'yes' if summary.clean else 'no'}",
            f"Staged: {summary.staged_count}",
            f"Modified: {summary.modified_count}",
            f"Untracked: {summary.untracked_count}",
            f"Conflicted: {summary.conflicted_count}",
        ]
    )
    return "\n".join(lines)


def render_repo_diff_text(summary: RepoDiffSummary) -> str:
    """Render a compact human-readable repository diff/stat summary."""
    title = "hahobot repo diff"
    if summary.staged:
        title += " --staged"
    if summary.name_only:
        title += " --name-only"

    lines = [
        title,
        "",
        f"Workspace: {summary.workspace}",
        f"Exists: {'yes' if summary.workspace_exists else 'no'}",
    ]
    if not summary.is_git_repo:
        lines.append("Git repo: no")
        if summary.error:
            lines.append(f"Error: {summary.error}")
        return "\n".join(lines)

    branch_label = (
        f"detached at {summary.head or 'unknown'}"
        if summary.detached
        else (summary.branch or "unknown")
    )
    lines.extend(
        [
            "Git repo: yes",
            f"Root: {summary.repo_root}",
            f"Branch: {branch_label}",
            f"Files changed: {summary.file_count}",
            f"Mode: {'staged' if summary.staged else 'working tree'} tracked diff",
            "",
            "Output:",
            summary.output,
        ]
    )
    return "\n".join(lines)
