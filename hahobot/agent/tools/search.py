"""Search tools: grep and glob."""

from __future__ import annotations

import asyncio
import fnmatch
import heapq
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

import regex as regex_engine

from hahobot.agent.tools.filesystem import ListDirTool, _FsTool

_DEFAULT_HEAD_LIMIT = 250
_MAX_CONCURRENT_SEARCH_WORKERS = 4
_SEARCH_WORKER_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT_SEARCH_WORKERS)
T = TypeVar("T")
_TYPE_GLOB_MAP = {
    "py": ("*.py", "*.pyi"),
    "python": ("*.py", "*.pyi"),
    "js": ("*.js", "*.jsx", "*.mjs", "*.cjs"),
    "ts": ("*.ts", "*.tsx", "*.mts", "*.cts"),
    "tsx": ("*.tsx",),
    "jsx": ("*.jsx",),
    "json": ("*.json",),
    "md": ("*.md", "*.mdx"),
    "markdown": ("*.md", "*.mdx"),
    "go": ("*.go",),
    "rs": ("*.rs",),
    "rust": ("*.rs",),
    "java": ("*.java",),
    "sh": ("*.sh", "*.bash"),
    "yaml": ("*.yaml", "*.yml"),
    "yml": ("*.yaml", "*.yml"),
    "toml": ("*.toml",),
    "sql": ("*.sql",),
    "html": ("*.html", "*.htm"),
    "css": ("*.css", "*.scss", "*.sass"),
}


class _SearchCancelledError(Exception):
    """Stop a worker scan after its owning async task is cancelled."""


class _SearchBudgetExceededError(Exception):
    """Stop a recursive scan after its time or path budget is exhausted."""


@dataclass(slots=True)
class _SearchBudget:
    cancelled: threading.Event
    deadline: float
    max_paths: int
    scanned_paths: int = 0

    def checkpoint(self) -> None:
        if self.cancelled.is_set():
            raise _SearchCancelledError
        if time.monotonic() >= self.deadline:
            raise _SearchBudgetExceededError("time")

    def visit_path(self) -> None:
        self.checkpoint()
        self.scanned_paths += 1
        if self.scanned_paths > self.max_paths:
            raise _SearchBudgetExceededError("paths")


def _normalize_pattern(pattern: str) -> str:
    return pattern.strip().replace("\\", "/")


def _match_glob(rel_path: str, name: str, pattern: str) -> bool:
    normalized = _normalize_pattern(pattern)
    if not normalized:
        return False
    if "/" in normalized or normalized.startswith("**"):
        return PurePosixPath(rel_path).match(normalized)
    return fnmatch.fnmatch(name, normalized)


def _is_binary(raw: bytes) -> bool:
    sample = raw[:4096]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    non_text = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return (non_text / len(sample)) > 0.2


def _paginate(items: list[T], limit: int | None, offset: int) -> tuple[list[T], bool]:
    if limit is None:
        return items[offset:], False
    sliced = items[offset : offset + limit]
    truncated = len(items) > offset + limit
    return sliced, truncated


def _pagination_note(limit: int | None, offset: int, truncated: bool) -> str | None:
    if truncated:
        if limit is None:
            return f"(pagination: offset={offset})"
        return f"(pagination: limit={limit}, offset={offset})"
    if offset > 0:
        return f"(pagination: offset={offset})"
    return None


def _matches_type(name: str, file_type: str | None) -> bool:
    if not file_type:
        return True
    lowered = file_type.strip().lower()
    if not lowered:
        return True
    patterns = _TYPE_GLOB_MAP.get(lowered, (f"*.{lowered}",))
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in patterns)


class _SearchTool(_FsTool):
    _IGNORE_DIRS = set(ListDirTool._IGNORE_DIRS)
    _MAX_SCAN_PATHS = 500_000
    _MAX_SCAN_SECONDS = 30.0

    def _display_path(self, target: Path, root: Path) -> str:
        if self._workspace:
            try:
                return target.relative_to(self._workspace).as_posix()
            except ValueError:
                pass
        return target.relative_to(root).as_posix()

    def _new_budget(self, cancelled: threading.Event) -> _SearchBudget:
        return _SearchBudget(
            cancelled=cancelled,
            deadline=time.monotonic() + self._MAX_SCAN_SECONDS,
            max_paths=self._MAX_SCAN_PATHS,
        )

    def _budget_error(self, tool_name: str, exc: _SearchBudgetExceededError) -> str:
        detail = (
            f"{self._MAX_SCAN_PATHS} paths"
            if str(exc) == "paths"
            else f"{self._MAX_SCAN_SECONDS:g} seconds"
        )
        return f"Error: {tool_name} scan exceeded {detail}; narrow path or filters and retry."

    async def _run_worker(
        self,
        func: Callable[..., str],
        args: tuple[Any, ...],
        *,
        cancelled: threading.Event,
        name: str,
    ) -> str:
        """Run one scan off-loop while retaining cooperative cancellation."""
        tool_name = name.removeprefix("hahobot-")
        worker_slots = _SEARCH_WORKER_SLOTS
        if not worker_slots.acquire(blocking=False):
            return (
                f"Error: {tool_name} search worker limit "
                f"({_MAX_CONCURRENT_SEARCH_WORKERS}) reached; retry shortly."
            )

        done = threading.Event()
        values: list[str] = []
        errors: list[Exception] = []

        def run() -> None:
            try:
                values.append(func(*args))
            except Exception as exc:
                errors.append(exc)
            finally:
                worker_slots.release()
                done.set()

        worker = threading.Thread(target=run, name=name, daemon=True)
        try:
            worker.start()
        except BaseException:
            worker_slots.release()
            raise
        deadline = time.monotonic() + self._MAX_SCAN_SECONDS
        try:
            while not done.is_set():
                if time.monotonic() >= deadline:
                    cancelled.set()
                    return self._budget_error(
                        tool_name,
                        _SearchBudgetExceededError("time"),
                    )
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        worker.join()
        if errors:
            raise errors[0]
        return values[0]

    def _walk_entries(
        self,
        root: Path,
        *,
        include_files: bool,
        include_dirs: bool,
        budget: _SearchBudget,
    ) -> Iterable[tuple[Path, bool]]:
        """Walk in stable path order without descending into directory symlinks."""
        budget.checkpoint()
        if root.is_file():
            budget.visit_path()
            if include_files:
                yield root, False
            return

        pending = [root]
        while pending:
            budget.checkpoint()
            directory = pending.pop()
            children: list[tuple[Path, bool, bool]] = []
            try:
                with os.scandir(directory) as raw_entries:
                    for raw_entry in raw_entries:
                        budget.visit_path()
                        try:
                            is_symlink = raw_entry.is_symlink()
                            is_real_dir = raw_entry.is_dir(follow_symlinks=False)
                            is_dir_link = (
                                not is_real_dir
                                and is_symlink
                                and raw_entry.is_dir(follow_symlinks=True)
                            )
                            is_display_file = (
                                not is_real_dir
                                and not is_dir_link
                                and (is_symlink or raw_entry.is_file(follow_symlinks=False))
                            )
                        except OSError:
                            continue
                        is_display_dir = is_real_dir or is_dir_link
                        if not (is_display_dir or is_display_file):
                            continue
                        if is_display_dir and raw_entry.name in self._IGNORE_DIRS:
                            continue
                        children.append((Path(raw_entry.path), is_display_dir, is_real_dir))
            except OSError:
                continue

            children.sort(key=lambda item: item[0].name)
            directories = [item for item in children if item[1]]
            files = [item for item in children if not item[1]]
            if include_dirs:
                for path, _, _ in directories:
                    yield path, True
            if include_files:
                for path, _, _ in files:
                    yield path, False
            pending.extend(path for path, _, is_real_dir in reversed(directories) if is_real_dir)

    def _iter_files(self, root: Path, *, budget: _SearchBudget) -> Iterable[Path]:
        for path, _ in self._walk_entries(
            root,
            include_files=True,
            include_dirs=False,
            budget=budget,
        ):
            yield path

    def _iter_entries(
        self,
        root: Path,
        *,
        include_files: bool,
        include_dirs: bool,
        budget: _SearchBudget,
    ) -> Iterable[tuple[Path, bool]]:
        yield from self._walk_entries(
            root,
            include_files=include_files,
            include_dirs=include_dirs,
            budget=budget,
        )


class GlobTool(_SearchTool):
    """Find files matching a glob pattern."""

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return (
            "Find files matching a glob pattern (e.g. '*.py', 'tests/**/test_*.py'). "
            "Results are sorted by modification time (newest first). "
            "Skips .git, node_modules, __pycache__, and other noise directories."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match, e.g. '*.py' or 'tests/**/test_*.py'",
                    "minLength": 1,
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search from (default '.')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Legacy alias for head_limit",
                    "minimum": 1,
                    "maximum": 1000,
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Maximum number of matches to return (default 250)",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N matching entries before returning results",
                    "minimum": 0,
                    "maximum": 100000,
                },
                "entry_type": {
                    "type": "string",
                    "enum": ["files", "dirs", "both"],
                    "description": "Whether to match files, directories, or both (default files)",
                },
            },
            "required": ["pattern"],
        }

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        entry_type: str = "files",
        **kwargs: Any,
    ) -> str:
        cancelled = threading.Event()
        try:
            return await self._run_worker(
                self._execute_sync,
                (pattern, path, max_results, head_limit, offset, entry_type, cancelled),
                cancelled=cancelled,
                name="hahobot-glob",
            )
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error finding files: {e}"

    def _execute_sync(
        self,
        pattern: str,
        path: str,
        max_results: int | None,
        head_limit: int | None,
        offset: int,
        entry_type: str,
        cancelled: threading.Event,
    ) -> str:
        root = self._resolve(path or ".")
        if not root.exists():
            return f"Error: Path not found: {path}"
        if not root.is_dir():
            return f"Error: Not a directory: {path}"

        if head_limit is not None:
            limit = None if head_limit == 0 else head_limit
        elif max_results is not None:
            limit = max_results
        else:
            limit = _DEFAULT_HEAD_LIMIT
        include_files = entry_type in {"files", "both"}
        include_dirs = entry_type in {"dirs", "both"}
        budget = self._new_budget(cancelled)

        def matching_entries() -> Iterable[tuple[str, float]]:
            for entry, is_dir in self._iter_entries(
                root,
                include_files=include_files,
                include_dirs=include_dirs,
                budget=budget,
            ):
                rel_path = entry.relative_to(root).as_posix()
                if not _match_glob(rel_path, entry.name, pattern):
                    continue
                display = self._display_path(entry, root)
                if is_dir:
                    display += "/"
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    mtime = 0.0
                yield display, mtime

        try:
            if limit is None:
                matches = sorted(matching_entries(), key=lambda item: (-item[1], item[0]))
            else:
                matches = heapq.nsmallest(
                    offset + limit + 1,
                    matching_entries(),
                    key=lambda item: (-item[1], item[0]),
                )
            budget.checkpoint()
        except _SearchBudgetExceededError as exc:
            return self._budget_error("glob", exc)

        if not matches:
            return f"No paths matched pattern '{pattern}' in {path}"

        ordered = [name for name, _ in matches]
        paged, truncated = _paginate(ordered, limit, offset)
        result = "\n".join(paged)
        if note := _pagination_note(limit, offset, truncated):
            result += f"\n\n{note}"
        return result


class GrepTool(_SearchTool):
    """Search file contents using a regex-like pattern."""

    _MAX_RESULT_CHARS = 128_000
    _MAX_FILE_BYTES = 2_000_000
    _MAX_PATTERN_CHARS = 10_000

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search file contents with a regex pattern. "
            "Default output_mode is files_with_matches (file paths only); "
            "use content mode for matching lines with context. "
            "Skips binary and files >2 MB. Supports glob/type filtering."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex or plain text pattern to search for",
                    "minLength": 1,
                    "maxLength": self._MAX_PATTERN_CHARS,
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (default '.')",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional file filter, e.g. '*.py' or 'tests/**/test_*.py'",
                },
                "type": {
                    "type": "string",
                    "description": "Optional file type shorthand, e.g. 'py', 'ts', 'md', 'json'",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default false)",
                },
                "fixed_strings": {
                    "type": "boolean",
                    "description": "Treat pattern as plain text instead of regex (default false)",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": (
                        "content: matching lines with optional context; "
                        "files_with_matches: only matching file paths; "
                        "count: matching line counts per file. "
                        "Default: files_with_matches"
                    ),
                },
                "context_before": {
                    "type": "integer",
                    "description": "Number of lines of context before each match",
                    "minimum": 0,
                    "maximum": 20,
                },
                "context_after": {
                    "type": "integer",
                    "description": "Number of lines of context after each match",
                    "minimum": 0,
                    "maximum": 20,
                },
                "max_matches": {
                    "type": "integer",
                    "description": ("Legacy alias for head_limit in content mode"),
                    "minimum": 1,
                    "maximum": 1000,
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Legacy alias for head_limit in files_with_matches or count mode"
                    ),
                    "minimum": 1,
                    "maximum": 1000,
                },
                "head_limit": {
                    "type": "integer",
                    "description": (
                        "Maximum number of results to return. In content mode this limits "
                        "matching line blocks; in other modes it limits file entries. "
                        "Default 250"
                    ),
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N results before applying head_limit",
                    "minimum": 0,
                    "maximum": 100000,
                },
            },
            "required": ["pattern"],
        }

    @staticmethod
    def _format_block(
        display_path: str,
        lines: list[str],
        match_line: int,
        before: int,
        after: int,
    ) -> str:
        start = max(1, match_line - before)
        end = min(len(lines), match_line + after)
        block = [f"{display_path}:{match_line}"]
        for line_no in range(start, end + 1):
            marker = ">" if line_no == match_line else " "
            block.append(f"{marker} {line_no}| {lines[line_no - 1]}")
        return "\n".join(block)

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        type: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int | None = None,
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        cancelled = threading.Event()
        try:
            return await self._run_worker(
                self._execute_sync,
                (
                    pattern,
                    path,
                    glob,
                    type,
                    case_insensitive,
                    fixed_strings,
                    output_mode,
                    context_before,
                    context_after,
                    max_matches,
                    max_results,
                    head_limit,
                    offset,
                    cancelled,
                ),
                cancelled=cancelled,
                name="hahobot-grep",
            )
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error searching files: {e}"

    def _execute_sync(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        type: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int | None = None,
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        cancelled: threading.Event | None = None,
    ) -> str:
        cancelled = cancelled or threading.Event()
        try:
            if len(pattern) > self._MAX_PATTERN_CHARS:
                return (
                    f"Error: regex pattern exceeds {self._MAX_PATTERN_CHARS} characters; "
                    "narrow the pattern and retry."
                )
            target = self._resolve(path or ".")
            if not target.exists():
                return f"Error: Path not found: {path}"
            if not (target.is_dir() or target.is_file()):
                return f"Error: Unsupported path: {path}"

            flags = regex_engine.IGNORECASE if case_insensitive else 0
            try:
                needle = regex_engine.escape(pattern) if fixed_strings else pattern
                matcher = regex_engine.compile(needle, flags | regex_engine.VERSION0)
            except regex_engine.error as e:
                return f"Error: invalid regex pattern: {e}"

            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif output_mode == "content" and max_matches is not None:
                limit = max_matches
            elif output_mode != "content" and max_results is not None:
                limit = max_results
            else:
                limit = _DEFAULT_HEAD_LIMIT
            blocks: list[str] = []
            result_chars = 0
            seen_content_matches = 0
            truncated = False
            size_truncated = False
            skipped_binary = 0
            skipped_large = 0
            matching_files: list[str] = []
            matching_files_set: set[str] = set()
            counts: dict[str, int] = {}
            file_mtimes: dict[str, float] = {}
            root = target if target.is_dir() else target.parent
            budget = self._new_budget(cancelled)

            for file_path in self._iter_files(target, budget=budget):
                budget.checkpoint()
                rel_path = file_path.relative_to(root).as_posix()
                if glob and not _match_glob(rel_path, file_path.name, glob):
                    continue
                if not _matches_type(file_path.name, type):
                    continue

                try:
                    readable_path = self._resolve(str(file_path))
                    if not readable_path.is_file():
                        skipped_binary += 1
                        continue
                    if readable_path.stat().st_size > self._MAX_FILE_BYTES:
                        skipped_large += 1
                        continue
                except (OSError, PermissionError):
                    skipped_binary += 1
                    continue
                raw = readable_path.read_bytes()
                budget.checkpoint()
                if _is_binary(raw):
                    skipped_binary += 1
                    continue
                try:
                    mtime = readable_path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    skipped_binary += 1
                    continue

                lines = content.splitlines()
                display_path = self._display_path(file_path, root)
                file_had_match = False
                for idx, line in enumerate(lines, start=1):
                    budget.checkpoint()
                    remaining = budget.deadline - time.monotonic()
                    try:
                        match = matcher.search(
                            line,
                            timeout=max(remaining, 0.000_001),
                            concurrent=True,
                        )
                    except TimeoutError as exc:
                        raise _SearchBudgetExceededError("time") from exc
                    if not match:
                        continue
                    file_had_match = True

                    if output_mode == "count":
                        counts[display_path] = counts.get(display_path, 0) + 1
                        continue
                    if output_mode == "files_with_matches":
                        if display_path not in matching_files_set:
                            matching_files_set.add(display_path)
                            matching_files.append(display_path)
                            file_mtimes[display_path] = mtime
                        break

                    seen_content_matches += 1
                    if seen_content_matches <= offset:
                        continue
                    if limit is not None and len(blocks) >= limit:
                        truncated = True
                        break
                    block = self._format_block(
                        display_path,
                        lines,
                        idx,
                        context_before,
                        context_after,
                    )
                    extra_sep = 2 if blocks else 0
                    if result_chars + extra_sep + len(block) > self._MAX_RESULT_CHARS:
                        size_truncated = True
                        break
                    blocks.append(block)
                    result_chars += extra_sep + len(block)
                if output_mode == "count" and file_had_match:
                    if display_path not in matching_files:
                        matching_files.append(display_path)
                        file_mtimes[display_path] = mtime
                if output_mode in {"count", "files_with_matches"} and file_had_match:
                    continue
                if truncated or size_truncated:
                    break

            if output_mode == "files_with_matches":
                if not matching_files:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    ordered_files = sorted(
                        matching_files,
                        key=lambda name: (-file_mtimes.get(name, 0.0), name),
                    )
                    paged, truncated = _paginate(ordered_files, limit, offset)
                    result = "\n".join(paged)
            elif output_mode == "count":
                if not counts:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    ordered_files = sorted(
                        matching_files,
                        key=lambda name: (-file_mtimes.get(name, 0.0), name),
                    )
                    ordered, truncated = _paginate(ordered_files, limit, offset)
                    lines = [f"{name}: {counts[name]}" for name in ordered]
                    result = "\n".join(lines)
            else:
                if not blocks:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    result = "\n\n".join(blocks)

            notes: list[str] = []
            if output_mode == "content" and truncated:
                notes.append(f"(pagination: limit={limit}, offset={offset})")
            elif output_mode == "content" and size_truncated:
                notes.append("(output truncated due to size)")
            elif truncated and output_mode in {"count", "files_with_matches"}:
                notes.append(f"(pagination: limit={limit}, offset={offset})")
            elif output_mode in {"count", "files_with_matches"} and offset > 0:
                notes.append(f"(pagination: offset={offset})")
            elif output_mode == "content" and offset > 0 and blocks:
                notes.append(f"(pagination: offset={offset})")
            if skipped_binary:
                notes.append(f"(skipped {skipped_binary} binary/unreadable files)")
            if skipped_large:
                notes.append(f"(skipped {skipped_large} large files)")
            if output_mode == "count" and counts:
                notes.append(f"(total matches: {sum(counts.values())} in {len(counts)} files)")
            if notes:
                result += "\n\n" + "\n".join(notes)
            return result
        except _SearchBudgetExceededError as e:
            return self._budget_error("grep", e)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error searching files: {e}"
