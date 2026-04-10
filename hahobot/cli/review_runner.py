"""Helpers for local diff-aware code review commands."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hahobot.cli.repo_inspector import inspect_repo_status
from hahobot.providers.base import LLMProvider

_DEFAULT_MAX_DIFF_CHARS = 32_000
_TRUNCATION_NOTICE = "\n\n[diff truncated for review prompt]"


@dataclass(frozen=True)
class ReviewInput:
    """Collected local repository diff payload for one review run."""

    workspace: Path
    workspace_exists: bool
    is_git_repo: bool
    repo_root: Path | None
    branch: str | None
    detached: bool
    head: str | None
    mode: str
    base: str | None
    path_filter: str | None
    clean: bool
    truncated: bool
    file_count: int
    files: tuple[str, ...]
    stat_output: str
    diff_output: str
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
            "mode": self.mode,
            "base": self.base,
            "path_filter": self.path_filter,
            "clean": self.clean,
            "truncated": self.truncated,
            "file_count": self.file_count,
            "files": list(self.files),
            "stat_output": self.stat_output,
            "diff_output": self.diff_output,
            "error": self.error,
        }


@dataclass(frozen=True)
class ReviewResult:
    """Rendered review response with the collected input summary."""

    request: ReviewInput
    model: str
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "model": self.model,
            "content": self.content,
        }


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


def _error_input(
    workspace: Path,
    *,
    mode: str,
    base: str | None,
    path_filter: str | None,
    error: str,
) -> ReviewInput:
    workspace = workspace.expanduser().resolve()
    return ReviewInput(
        workspace=workspace,
        workspace_exists=workspace.exists(),
        is_git_repo=False,
        repo_root=None,
        branch=None,
        detached=False,
        head=None,
        mode=mode,
        base=base,
        path_filter=path_filter,
        clean=True,
        truncated=False,
        file_count=0,
        files=(),
        stat_output="",
        diff_output="",
        error=error,
    )


def _normalize_path_filter(repo_root: Path, path_filter: str | None) -> str | None:
    if not path_filter:
        return None
    raw = Path(path_filter).expanduser()
    candidate = raw if raw.is_absolute() else repo_root / raw
    try:
        relative = candidate.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError("Path filter must stay inside the repository.") from exc
    normalized = relative.as_posix()
    if not normalized or normalized == ".":
        return None
    return normalized


def collect_review_input(
    workspace: Path,
    *,
    staged: bool = False,
    base: str | None = None,
    path_filter: str | None = None,
    max_diff_chars: int = _DEFAULT_MAX_DIFF_CHARS,
) -> ReviewInput:
    """Collect repository metadata plus a local diff payload for review."""
    if staged and base:
        raise ValueError("Choose only one of staged or base review mode.")

    mode = f"base:{base}" if base else ("staged" if staged else "working_tree")
    status = inspect_repo_status(workspace)
    if not status.is_git_repo:
        return _error_input(
            status.workspace,
            mode=mode,
            base=base,
            path_filter=path_filter,
            error=status.error or "Not a git repository.",
        )

    try:
        normalized_path = _normalize_path_filter(status.repo_root or status.workspace, path_filter)
    except ValueError as exc:
        return _error_input(
            status.workspace,
            mode=mode,
            base=base,
            path_filter=path_filter,
            error=str(exc),
        )

    base_args = ["diff", "--no-ext-diff"]
    if base:
        base_args.append(base)
    elif staged:
        base_args.append("--cached")

    file_args = [*base_args, "--name-only"]
    stat_args = [*base_args, "--stat"]
    patch_args = [*base_args, "--no-color", "--unified=3"]
    if normalized_path:
        file_args.extend(["--", normalized_path])
        stat_args.extend(["--", normalized_path])
        patch_args.extend(["--", normalized_path])

    file_result = _run_git(status.workspace, *file_args)
    stat_result = _run_git(status.workspace, *stat_args)
    patch_result = _run_git(status.workspace, *patch_args)
    for result in (file_result, stat_result, patch_result):
        if result is None or result.returncode != 0:
            return _error_input(
                status.workspace,
                mode=mode,
                base=base,
                path_filter=normalized_path or path_filter,
                error=_normalize_git_error(result),
            )

    files = tuple(line.strip() for line in file_result.stdout.splitlines() if line.strip())
    stat_output = stat_result.stdout.strip()
    diff_output = patch_result.stdout.strip()
    clean = not files
    truncated = False
    if diff_output and len(diff_output) > max_diff_chars:
        diff_output = diff_output[: max_diff_chars - len(_TRUNCATION_NOTICE)].rstrip() + _TRUNCATION_NOTICE
        truncated = True
    if not stat_output:
        stat_output = "No diff stat output."
    if not diff_output and not clean:
        diff_output = "[no textual patch output; possible binary or metadata-only change]"

    return ReviewInput(
        workspace=status.workspace,
        workspace_exists=status.workspace_exists,
        is_git_repo=status.is_git_repo,
        repo_root=status.repo_root,
        branch=status.branch,
        detached=status.detached,
        head=status.head,
        mode=mode,
        base=base,
        path_filter=normalized_path,
        clean=clean,
        truncated=truncated,
        file_count=len(files),
        files=files,
        stat_output=stat_output,
        diff_output=diff_output,
        error=None,
    )


def build_review_messages(payload: ReviewInput) -> list[dict[str, str]]:
    """Build a tool-free review prompt for the configured model provider."""
    branch_label = f"detached at {payload.head or 'unknown'}" if payload.detached else (
        payload.branch or "unknown"
    )
    files_text = "\n".join(f"- {path}" for path in payload.files) or "- [none]"
    path_line = payload.path_filter or "[none]"
    diff_block = payload.diff_output or "[empty diff]"
    return [
        {
            "role": "system",
            "content": (
                "You are reviewing a local Git change set. Focus on correctness bugs, "
                "regressions, security issues, data loss risks, and missing tests. "
                "Ignore style-only nits unless they hide a real behavioral problem.\n\n"
                "Return findings first.\n"
                "- If there are findings, start with 'Findings:' and use flat bullets.\n"
                "- Each finding bullet must include a severity tag of [high], [medium], or [low] "
                "and at least one concrete file/path reference.\n"
                "- After findings, you may add 'Residual risks:' with short bullets.\n"
                "- If there are no concrete findings, reply with 'No findings.' and optionally add "
                "a short 'Residual risks:' section.\n"
                "Base the review only on the diff and metadata provided. Do not claim to have run "
                "tests or executed the code."
            ),
        },
        {
            "role": "user",
            "content": (
                "Review this local repository diff.\n\n"
                f"Repository root: {payload.repo_root}\n"
                f"Workspace: {payload.workspace}\n"
                f"Branch: {branch_label}\n"
                f"Review mode: {payload.mode}\n"
                f"Path filter: {path_line}\n"
                f"Changed files ({payload.file_count}):\n{files_text}\n\n"
                "Diff stat:\n"
                f"{payload.stat_output}\n\n"
                "Unified diff:\n"
                f"```diff\n{diff_block}\n```"
            ),
        },
    ]


async def run_review(
    *,
    provider: LLMProvider,
    model: str,
    workspace: Path,
    staged: bool = False,
    base: str | None = None,
    path_filter: str | None = None,
    retry_mode: str = "standard",
) -> ReviewResult:
    """Collect local diff context and ask the configured provider for a review."""
    payload = collect_review_input(
        workspace,
        staged=staged,
        base=base,
        path_filter=path_filter,
    )
    return await run_review_for_input(
        provider=provider,
        model=model,
        payload=payload,
        retry_mode=retry_mode,
    )


async def run_review_for_input(
    *,
    provider: LLMProvider,
    model: str,
    payload: ReviewInput,
    retry_mode: str = "standard",
) -> ReviewResult:
    """Run provider-backed review for an already collected diff payload."""
    if payload.error:
        return ReviewResult(request=payload, model=model, content=payload.error)
    if payload.clean:
        return ReviewResult(request=payload, model=model, content="No diff to review.")

    response = await provider.chat_with_retry(
        messages=build_review_messages(payload),
        tools=None,
        model=model,
        retry_mode=retry_mode,
    )
    content = (response.content or "").strip() or "No findings."
    return ReviewResult(request=payload, model=model, content=content)
