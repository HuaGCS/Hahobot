"""Memory system: pure file I/O store, lightweight Consolidator, and Dream processor."""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import weakref
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from hahobot.agent.history_archive import HistoryArchiveStore
from hahobot.agent.i18n import DEFAULT_LANGUAGE, resolve_language
from hahobot.agent.memory_facts_sqlite import extract_fragment_header, parse_memory_fragments
from hahobot.agent.memory_metadata import format_memory_metadata_summary
from hahobot.agent.personas import (
    DEFAULT_PERSONA,
    list_personas,
    persona_workspace,
    resolve_persona_name,
)
from hahobot.agent.privacy import strip_private_text
from hahobot.agent.runner import AgentRunner, AgentRunSpec
from hahobot.agent.tools.registry import ToolRegistry
from hahobot.utils.gitstore import GitStore
from hahobot.utils.helpers import (
    ensure_dir,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    strip_think,
    truncate_text,
)
from hahobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from hahobot.providers.base import LLMProvider
    from hahobot.session.manager import Session, SessionManager


_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "new_facts": {
                        "type": "string",
                        "description": "Only durable facts newly learned in this conversation. "
                        "Format: one fragment per blank-line-separated block. Optionally prefix "
                        "each block with a tag header on its own line, e.g. "
                        "'<!-- tag:preference -->' (tag is one of preference, project, reference, "
                        "feedback, user, experience). Use 'experience' only for a successful task "
                        "pattern that should guide future similar requests (sequence of tools, "
                        "common pitfall, workaround). Leave empty if nothing new. Do NOT restate "
                        "or rewrite existing memory; deduplication and cleanup happen later "
                        "during Dream reflection.",
                    },
                },
                "required": ["history_entry"],
            },
        },
    }
]


def _ensure_text(value: Any) -> str:
    """Normalize tool-call payload values to text for file storage."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _normalize_save_memory_args(args: Any) -> dict[str, Any] | None:
    """Normalize provider tool-call arguments to the expected dict shape."""
    if isinstance(args, str):
        args = json.loads(args)
    if isinstance(args, list):
        return args[0] if args and isinstance(args[0], dict) else None
    return args if isinstance(args, dict) else None


_TOOL_CHOICE_ERROR_MARKERS = (
    "tool_choice",
    "toolchoice",
    "does not support",
    'should be ["none", "auto"]',
)

_RAW_ARCHIVE_MAX_CHARS = 16_000
_ARCHIVE_SUMMARY_MAX_CHARS = 8_000
_HISTORY_ENTRY_HARD_CAP = 64_000
_MEMORY_APPEND_MAX_CHARS = 4_000

_FRAGMENT_TAG_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_FRAGMENT_DEFAULT_TAG = "preference"
_FRAGMENT_KNOWN_TAGS = frozenset(
    {"preference", "project", "reference", "feedback", "user", "experience", "legacy"}
)


def _normalize_tag(tag: str | None) -> str:
    """Coerce *tag* into a safe lowercase enum token; fall back to default."""
    if not tag:
        return _FRAGMENT_DEFAULT_TAG
    token = tag.strip().lower()
    if not _FRAGMENT_TAG_RE.match(token):
        return _FRAGMENT_DEFAULT_TAG
    if token not in _FRAGMENT_KNOWN_TAGS:
        return _FRAGMENT_DEFAULT_TAG
    return token


def _format_new_facts_as_fragments(text: str, *, src: str, now_iso: str) -> str:
    """Wrap each blank-line-separated block with a server-controlled metadata header.

    LLM-emitted ``<!-- tag:WORD -->`` headers are honored for the ``tag`` field;
    ``ts`` and ``src`` are always overwritten with server values so the persisted
    fragments carry trustworthy provenance.
    """
    stripped = (text or "").strip()
    if not stripped:
        return ""
    blocks = [block for block in re.split(r"\n\s*\n", stripped) if block.strip()]
    formatted: list[str] = []
    for block in blocks:
        first_line, _, remainder = block.partition("\n")
        tokens = extract_fragment_header(first_line)
        if tokens is not None:
            tag = _normalize_tag(tokens.get("tag"))
            body = remainder.strip()
        else:
            tag = _FRAGMENT_DEFAULT_TAG
            body = block.strip()
        if not body:
            continue
        header = f"<!-- ts:{now_iso} tag:{tag} src:{src} -->"
        formatted.append(f"{header}\n{body}")
    return "\n\n".join(formatted)


def _is_tool_choice_unsupported(content: str | None) -> bool:
    """Detect provider errors caused by forced tool_choice being unsupported."""
    text = (content or "").lower()
    return any(marker in text for marker in _TOOL_CHOICE_ERROR_MARKERS)


def _atomic_write_text(path: Path, content: str) -> None:
    """Write *content* to *path* atomically: write a temp sibling, then os.replace."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def migrate_legacy_memory_file(
    path: Path,
    *,
    src: str = "migration",
    backup: bool = True,
) -> dict[str, Any]:
    """Rewrite *path* so every fragment carries a structured header.

    Legacy fragments (those without a recognized ``<!-- ts:... tag:... src:... -->``
    header) are wrapped with ``tag:legacy src:{src}`` and the file's mtime as ``ts``.
    Already-structured fragments are re-emitted unchanged. The file is left
    untouched (and ``changed=False``) when there is nothing legacy to migrate.

    Returns a summary dict with ``path``, ``exists``, ``migrated``, ``preserved``,
    ``changed``, and an optional ``backup`` path.
    """
    summary: dict[str, Any] = {
        "path": str(path),
        "exists": False,
        "migrated": 0,
        "preserved": 0,
        "changed": False,
        "backup": None,
    }
    try:
        text = path.read_text(encoding="utf-8")
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError:
        return summary
    summary["exists"] = True
    default_ts = datetime.fromtimestamp(mtime_ns / 1_000_000_000).strftime("%Y-%m-%dT%H:%M")
    fragments = parse_memory_fragments(text, default_ts=default_ts)
    if not fragments:
        return summary

    parts: list[str] = []
    migrated = 0
    preserved = 0
    for fragment in fragments:
        is_legacy = fragment["tag"] == "legacy" and fragment["src"] == "unknown"
        if is_legacy:
            header = f"<!-- ts:{fragment['ts']} tag:legacy src:{src} -->"
            migrated += 1
        else:
            header = f"<!-- ts:{fragment['ts']} tag:{fragment['tag']} src:{fragment['src']} -->"
            preserved += 1
        parts.append(f"{header}\n{fragment['fragment']}")
    summary["migrated"] = migrated
    summary["preserved"] = preserved

    if migrated == 0:
        return summary

    rewritten = "\n\n".join(parts) + "\n"
    if rewritten == text:
        return summary

    if backup:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_path = path.with_name(f"{path.name}.bak.{stamp}")
        backup_path.write_text(text, encoding="utf-8")
        summary["backup"] = str(backup_path)
    _atomic_write_text(path, rewritten)
    summary["changed"] = True
    return summary


def migrate_legacy_memory_workspace(workspace: Path) -> dict[str, Any]:
    """Migrate every persona's ``memory/MEMORY.md`` under *workspace*."""
    personas: list[str | None] = [None]
    personas.extend(list_personas(workspace))

    results: list[dict[str, Any]] = []
    total_migrated = 0
    total_preserved = 0
    files_changed = 0
    seen: set[Path] = set()
    for persona in personas:
        pw = persona_workspace(workspace, persona)
        memory_file = pw / "memory" / "MEMORY.md"
        if memory_file in seen:
            continue
        seen.add(memory_file)
        summary = migrate_legacy_memory_file(memory_file)
        summary["persona"] = persona or DEFAULT_PERSONA
        results.append(summary)
        if summary["changed"]:
            files_changed += 1
        total_migrated += summary.get("migrated", 0)
        total_preserved += summary.get("preserved", 0)
    return {
        "total_migrated": total_migrated,
        "total_preserved": total_preserved,
        "files_changed": files_changed,
        "personas_checked": len(seen),
        "results": results,
    }


# ---------------------------------------------------------------------------
# MemoryStore — pure file I/O layer
# ---------------------------------------------------------------------------


class MemoryStore:
    """Pure file I/O for memory files: MEMORY.md, history.jsonl, SOUL.md, USER.md, PROFILE.md, INSIGHTS.md."""

    _DEFAULT_MAX_HISTORY = 1000
    _MAX_FAILURES_BEFORE_RAW_ARCHIVE = 3
    _LEGACY_ENTRY_START_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}[^\]]*)\]\s*")
    _LEGACY_TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]\s*")
    _LEGACY_RAW_MESSAGE_RE = re.compile(
        r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s+[A-Z][A-Z0-9_]*(?:\s+\[tools:\s*[^\]]+\])?:"
    )

    def __init__(self, workspace: Path, max_history_entries: int = _DEFAULT_MAX_HISTORY):
        self.workspace = workspace
        self.max_history_entries = max_history_entries
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "history.jsonl"
        self.legacy_history_file = self.memory_dir / "HISTORY.md"
        self.soul_file = workspace / "SOUL.md"
        self.user_file = workspace / "USER.md"
        self.profile_file = workspace / "PROFILE.md"
        self.insights_file = workspace / "INSIGHTS.md"
        self._cursor_file = self.memory_dir / ".cursor"
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"
        self._consecutive_failures = 0
        self._malformed_entry_logged = False  # rate-limit bad history shape warning
        self._git = GitStore(
            workspace,
            tracked_files=[
                "SOUL.md",
                "USER.md",
                "PROFILE.md",
                "INSIGHTS.md",
                "memory/MEMORY.md",
            ],
            seed_missing_files=False,
        )
        self._maybe_migrate_legacy_history()

    @property
    def git(self) -> GitStore:
        return self._git

    # -- generic helpers -----------------------------------------------------

    @staticmethod
    def read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def _maybe_migrate_legacy_history(self) -> None:
        """One-time upgrade from legacy HISTORY.md to history.jsonl.

        The migration is best-effort and prioritizes preserving as much content
        as possible over perfect parsing.
        """
        if not self.legacy_history_file.exists():
            return
        if self.history_file.exists() and self.history_file.stat().st_size > 0:
            return

        try:
            legacy_text = self.legacy_history_file.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            logger.exception("Failed to read legacy HISTORY.md for migration")
            return

        entries = self._parse_legacy_history(legacy_text)
        try:
            if entries:
                self._write_entries(entries)
                last_cursor = entries[-1]["cursor"]
                self._cursor_file.write_text(str(last_cursor), encoding="utf-8")
                # Default to "already processed" so upgrades do not replay the
                # user's entire historical archive into Dream on first start.
                self._dream_cursor_file.write_text(str(last_cursor), encoding="utf-8")

            backup_path = self._next_legacy_backup_path()
            self.legacy_history_file.replace(backup_path)
            logger.info(
                "Migrated legacy HISTORY.md to history.jsonl ({} entries)",
                len(entries),
            )
        except Exception:
            logger.exception("Failed to migrate legacy HISTORY.md")

    def _parse_legacy_history(self, text: str) -> list[dict[str, Any]]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []

        fallback_timestamp = self._legacy_fallback_timestamp()
        entries: list[dict[str, Any]] = []
        chunks = self._split_legacy_history_chunks(normalized)

        for cursor, chunk in enumerate(chunks, start=1):
            timestamp = fallback_timestamp
            content = chunk
            match = self._LEGACY_TIMESTAMP_RE.match(chunk)
            if match:
                timestamp = match.group(1)
                remainder = chunk[match.end() :].lstrip()
                if remainder:
                    content = remainder

            entries.append(
                {
                    "cursor": cursor,
                    "timestamp": timestamp,
                    "content": content,
                }
            )
        return entries

    def _split_legacy_history_chunks(self, text: str) -> list[str]:
        lines = text.split("\n")
        chunks: list[str] = []
        current: list[str] = []
        saw_blank_separator = False

        for line in lines:
            if saw_blank_separator and line.strip() and current:
                chunks.append("\n".join(current).strip())
                current = [line]
                saw_blank_separator = False
                continue
            if self._should_start_new_legacy_chunk(line, current):
                chunks.append("\n".join(current).strip())
                current = [line]
                saw_blank_separator = False
                continue
            current.append(line)
            saw_blank_separator = not line.strip()

        if current:
            chunks.append("\n".join(current).strip())
        return [chunk for chunk in chunks if chunk]

    def _should_start_new_legacy_chunk(self, line: str, current: list[str]) -> bool:
        if not current:
            return False
        if not self._LEGACY_ENTRY_START_RE.match(line):
            return False
        if self._is_raw_legacy_chunk(current) and self._LEGACY_RAW_MESSAGE_RE.match(line):
            return False
        return True

    def _is_raw_legacy_chunk(self, lines: list[str]) -> bool:
        first_nonempty = next((line for line in lines if line.strip()), "")
        match = self._LEGACY_TIMESTAMP_RE.match(first_nonempty)
        if not match:
            return False
        return first_nonempty[match.end() :].lstrip().startswith("[RAW]")

    def _legacy_fallback_timestamp(self) -> str:
        try:
            return datetime.fromtimestamp(
                self.legacy_history_file.stat().st_mtime,
            ).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return datetime.now().strftime("%Y-%m-%d %H:%M")

    def _next_legacy_backup_path(self) -> Path:
        candidate = self.memory_dir / "HISTORY.md.bak"
        suffix = 2
        while candidate.exists():
            candidate = self.memory_dir / f"HISTORY.md.bak.{suffix}"
            suffix += 1
        return candidate

    # -- MEMORY.md (long-term facts) -----------------------------------------

    def read_memory(self) -> str:
        return self.read_file(self.memory_file)

    def write_memory(self, content: str) -> None:
        _atomic_write_text(self.memory_file, content)

    def append_memory(self, fragment: str, *, max_chars: int = _MEMORY_APPEND_MAX_CHARS) -> None:
        """Append newly learned facts to MEMORY.md.

        Append-only by design: a malformed or truncated consolidation can never
        overwrite existing memory. Deduplication and compaction are left to Dream
        reflection, which edits the file incrementally.
        """
        fragment = strip_private_text(fragment.strip())
        if not fragment:
            return
        if len(fragment) > max_chars:
            logger.warning(
                "Memory append fragment exceeded {} chars ({}); truncating",
                max_chars,
                len(fragment),
            )
            fragment = truncate_text(fragment, max_chars)
        current = self.read_memory().rstrip()
        combined = (current + "\n\n" + fragment + "\n") if current else (fragment + "\n")
        self.write_memory(combined)

    def read_long_term(self) -> str:
        """Backward-compatible alias for older callers."""
        return self.read_memory()

    # -- SOUL.md -------------------------------------------------------------

    def read_soul(self) -> str:
        return self.read_file(self.soul_file)

    def write_soul(self, content: str) -> None:
        self.soul_file.write_text(content, encoding="utf-8")

    # -- USER.md -------------------------------------------------------------

    def read_user(self) -> str:
        return self.read_file(self.user_file)

    def write_user(self, content: str) -> None:
        self.user_file.write_text(content, encoding="utf-8")

    # -- PROFILE.md ----------------------------------------------------------

    def read_profile(self) -> str:
        return self.read_file(self.profile_file)

    def write_profile(self, content: str) -> None:
        self.profile_file.write_text(content, encoding="utf-8")

    # -- INSIGHTS.md --------------------------------------------------------

    def read_insights(self) -> str:
        return self.read_file(self.insights_file)

    def write_insights(self, content: str) -> None:
        self.insights_file.write_text(content, encoding="utf-8")

    # -- context injection (used by context.py) ------------------------------

    def get_memory_context(self) -> str:
        long_term = self.read_memory()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    # -- history.jsonl — append-only, JSONL format ---------------------------

    def append_history(self, entry: str, *, max_chars: int | None = None) -> int:
        """Append *entry* to history.jsonl and return its auto-incrementing cursor."""
        cursor = self._next_cursor()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        raw = strip_private_text(entry.rstrip())
        limit = max_chars if max_chars is not None else _HISTORY_ENTRY_HARD_CAP
        if len(raw) > limit:
            logger.warning("History entry exceeded {} chars ({}); truncating", limit, len(raw))
            raw = truncate_text(raw, limit)
        content = strip_think(raw) or raw
        record = {"cursor": cursor, "timestamp": ts, "content": content}
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key not in {"cursor", "timestamp", "content"}:
                    record[key] = value
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._cursor_file.write_text(str(cursor), encoding="utf-8")
        return cursor

    @staticmethod
    def _valid_cursor(value: Any) -> int | None:
        """Non-negative int cursors only; reject bool (``isinstance(True, int)`` is True)."""
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    def _read_cursor_counter(self) -> int | None:
        """Return the persisted ``.cursor`` counter when it is a usable non-negative int."""
        if not self._cursor_file.exists():
            return None
        try:
            cursor = int(self._cursor_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None
        return cursor if cursor >= 0 else None

    def _max_history_cursor(self) -> int:
        return max((e["cursor"] for e in self._read_entries()), default=0)

    def _next_cursor(self) -> int:
        """Read the current cursor counter and return the next value.

        The allocated cursor must stay strictly monotonic even if the ``.cursor``
        sidecar lags behind history.jsonl (truncation, external writers) or carries
        a corrupt / negative value, otherwise ``read_unprocessed_history`` / dream /
        consolidation would re-process or skip entries. So take the max of the
        ``.cursor`` counter and the history tail rather than trusting either alone.
        """
        cursor_counter = self._read_cursor_counter()
        last = self._read_last_entry() or {}
        last_cursor = self._valid_cursor(last.get("cursor"))
        if cursor_counter is not None:
            if last_cursor is not None:
                return max(cursor_counter, last_cursor) + 1
            return max(cursor_counter, self._max_history_cursor()) + 1
        if last_cursor is not None:
            return last_cursor + 1
        return self._max_history_cursor() + 1

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        """Return history entries with cursor > *since_cursor*."""
        return [e for e in self._read_entries() if e["cursor"] > since_cursor]

    def compact_history(self) -> None:
        """Drop oldest entries if the file exceeds *max_history_entries*."""
        if self.max_history_entries <= 0:
            return
        entries = self._read_entries()
        if len(entries) <= self.max_history_entries:
            return
        kept = entries[-self.max_history_entries :]
        self._write_entries(kept)

    # -- JSONL helpers -------------------------------------------------------

    def _read_entries(self) -> list[dict[str, Any]]:
        """Read all well-formed entries from history.jsonl.

        Entries that parse as JSON but carry a malformed shape (missing int
        cursor, non-string timestamp/content) are dropped so an external writer
        cannot crash history slicing, dream, or consolidation. Warns once.
        """
        entries: list[dict[str, Any]] = []
        malformed = False
        try:
            with open(self.history_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not self._valid_history_payload(entry):
                        malformed = True
                        continue
                    entries.append(entry)
        except FileNotFoundError:
            pass
        if malformed and not self._malformed_entry_logged:
            self._malformed_entry_logged = True
            logger.warning(
                "history.jsonl contains a malformed entry; dropping it. "
                "Usually caused by an external writer; further occurrences suppressed."
            )
        return entries

    @staticmethod
    def _valid_history_payload(entry: Any) -> bool:
        if not isinstance(entry, dict):
            return False
        # Non-negative int cursor only (reject bool / negative); read_unprocessed_history
        # filters on ``cursor > since`` and a negative cursor would break that ordering.
        if MemoryStore._valid_cursor(entry.get("cursor")) is None:
            return False
        if not isinstance(entry.get("timestamp"), str):
            return False
        return isinstance(entry.get("content"), str)

    def _read_last_entry(self) -> dict[str, Any] | None:
        """Read the last entry from the JSONL file efficiently."""
        try:
            with open(self.history_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return None
                read_size = min(size, 4096)
                f.seek(size - read_size)
                data = f.read().decode("utf-8")
                lines = [line for line in data.split("\n") if line.strip()]
                if not lines:
                    return None
                return json.loads(lines[-1])
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        """Overwrite history.jsonl with the given entries."""
        with open(self.history_file, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- dream cursor --------------------------------------------------------

    def get_last_dream_cursor(self) -> int:
        if self._dream_cursor_file.exists():
            try:
                return int(self._dream_cursor_file.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pass
        return 0

    def set_last_dream_cursor(self, cursor: int) -> None:
        self._dream_cursor_file.write_text(str(cursor), encoding="utf-8")

    # -- message formatting utility ------------------------------------------

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = (
                f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            )
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    async def consolidate(
        self,
        messages: list[dict],
        provider: LLMProvider,
        model: str,
        *,
        on_archive: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        """Consolidate the provided message chunk into MEMORY.md + HISTORY.md."""
        if not messages:
            return True

        current_memory = self.read_long_term()
        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

Write `history_entry` summarizing what happened. In `new_facts`, list ONLY durable facts newly
learned in this conversation that are not already in current memory below — leave it empty if
there is nothing new. Do not restate or rewrite existing memory.

Fragment format for `new_facts`:
- One fragment per blank-line-separated block.
- Optionally prefix each block with a tag on its own line: `<!-- tag:WORD -->`
  where WORD is one of preference, project, reference, feedback, user, experience.
- Use `experience` only for a successful task pattern worth reusing later
  (e.g. "for image-to-text tasks, run image_gen → ffmpeg → web_search"),
  not for plain facts.
- Server-side ts and src are filled in automatically — do not emit them.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{self._format_messages(messages)}"""

        chat_messages = [
            {
                "role": "system",
                "content": "You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation.",
            },
            {"role": "user", "content": prompt},
        ]

        try:
            forced = {"type": "function", "function": {"name": "save_memory"}}
            response = await provider.chat_with_retry(
                messages=chat_messages,
                tools=_SAVE_MEMORY_TOOL,
                model=model,
                tool_choice=forced,
            )

            if response.finish_reason == "error" and _is_tool_choice_unsupported(response.content):
                logger.warning("Forced tool_choice unsupported, retrying with auto")
                response = await provider.chat_with_retry(
                    messages=chat_messages,
                    tools=_SAVE_MEMORY_TOOL,
                    model=model,
                    tool_choice="auto",
                )

            if not response.has_tool_calls:
                logger.warning(
                    "Memory consolidation: LLM did not call save_memory "
                    "(finish_reason={}, content_len={}, content_preview={})",
                    response.finish_reason,
                    len(response.content or ""),
                    (response.content or "")[:200],
                )
                return self._fail_or_raw_archive(messages, on_archive=on_archive)

            args = _normalize_save_memory_args(response.tool_calls[0].arguments)
            if args is None:
                logger.warning("Memory consolidation: unexpected save_memory arguments")
                return self._fail_or_raw_archive(messages, on_archive=on_archive)

            if "history_entry" not in args:
                logger.warning("Memory consolidation: save_memory payload missing history_entry")
                return self._fail_or_raw_archive(messages, on_archive=on_archive)

            entry = args["history_entry"]
            if entry is None:
                logger.warning("Memory consolidation: save_memory payload has null history_entry")
                return self._fail_or_raw_archive(messages, on_archive=on_archive)

            entry = _ensure_text(entry).strip()
            if not entry:
                logger.warning("Memory consolidation: history_entry is empty after normalization")
                return self._fail_or_raw_archive(messages, on_archive=on_archive)

            self.append_history(entry, max_chars=_ARCHIVE_SUMMARY_MAX_CHARS)
            new_facts = _ensure_text(args.get("new_facts") or "").strip()
            if new_facts:
                wrapped = _format_new_facts_as_fragments(
                    new_facts,
                    src="turn",
                    now_iso=datetime.now().strftime("%Y-%m-%dT%H:%M"),
                )
                self.append_memory(wrapped or new_facts)
            if on_archive is not None:
                try:
                    on_archive(
                        {
                            "history_entry": entry,
                            "new_facts": new_facts,
                            "raw_archive": False,
                        }
                    )
                except Exception:
                    logger.exception("History archive callback failed after consolidation")

            self._consecutive_failures = 0
            logger.info("Memory consolidation done for {} messages", len(messages))
            return True
        except Exception:
            logger.exception("Memory consolidation failed")
            return self._fail_or_raw_archive(messages, on_archive=on_archive)

    def _fail_or_raw_archive(
        self,
        messages: list[dict],
        *,
        on_archive: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        """Increment failure count; after threshold, raw-archive messages and return True."""
        self._consecutive_failures += 1
        if self._consecutive_failures < self._MAX_FAILURES_BEFORE_RAW_ARCHIVE:
            return False
        self._raw_archive(messages, on_archive=on_archive)
        self._consecutive_failures = 0
        return True

    def _raw_archive(
        self,
        messages: list[dict],
        *,
        on_archive: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Fallback: dump raw messages to HISTORY.md without LLM summarization."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        formatted = truncate_text(self._format_messages(messages), _RAW_ARCHIVE_MAX_CHARS)
        entry = f"[{ts}] [RAW] {len(messages)} messages\n{formatted}"
        self.append_history(entry)
        if on_archive is not None:
            try:
                on_archive(
                    {
                        "history_entry": entry,
                        "new_facts": "",
                        "raw_archive": True,
                    }
                )
            except Exception:
                logger.exception("History archive callback failed after raw archive fallback")
        logger.warning("Memory consolidation degraded: raw-archived {} messages", len(messages))

    def raw_archive(self, messages: list[dict]) -> None:
        """Backward-compatible raw archive entry point."""
        self._raw_archive(messages)


# ---------------------------------------------------------------------------
# Consolidator — lightweight token-budget triggered consolidation
# ---------------------------------------------------------------------------


class Consolidator:
    """Lightweight consolidation: summarizes evicted messages into history.jsonl."""

    _MAX_CONSOLIDATION_ROUNDS = 5

    _SAFETY_BUFFER = 1024  # extra headroom for tokenizer estimation drift

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        max_completion_tokens: int = 4096,
    ):
        self.store = store
        self.workspace = store.workspace
        self.provider = provider
        self.model = model
        self.sessions = sessions
        self.context_window_tokens = context_window_tokens
        self.max_completion_tokens = max_completion_tokens
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._stores: dict[Path, MemoryStore] = {}
        self._active_session: contextvars.ContextVar[Session | None] = contextvars.ContextVar(
            "memory_consolidation_session",
            default=None,
        )
        self._archive_callbacks: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = (
            contextvars.ContextVar("memory_archive_callback", default=None)
        )
        self._archive_stores: dict[Path, HistoryArchiveStore] = {}

    def _get_persona(self, session: Session) -> str:
        """Resolve the active persona for a session."""
        return (
            resolve_persona_name(self.workspace, session.metadata.get("persona")) or DEFAULT_PERSONA
        )

    def _get_language(self, session: Session) -> str:
        """Resolve the active language for a session."""
        metadata = getattr(session, "metadata", {})
        raw = metadata.get("language") if isinstance(metadata, dict) else DEFAULT_LANGUAGE
        return resolve_language(raw)

    def _get_store(self, session: Session) -> MemoryStore:
        """Return the memory store associated with the active persona."""
        store_root = persona_workspace(self.workspace, self._get_persona(session))
        return self._stores.setdefault(store_root, MemoryStore(store_root))

    def _get_default_store(self) -> MemoryStore:
        """Return the default persona store for session-less consolidation contexts."""
        store_root = persona_workspace(self.workspace, DEFAULT_PERSONA)
        return self._stores.setdefault(store_root, MemoryStore(store_root))

    def _get_archive_store(self, session: Session | None) -> HistoryArchiveStore:
        """Return the structured archive store for the active persona."""
        persona = self._get_persona(session) if session is not None else DEFAULT_PERSONA
        store_root = persona_workspace(self.workspace, persona)
        return self._archive_stores.setdefault(
            store_root, HistoryArchiveStore(self.workspace, persona)
        )

    def rebind_runtime(self, *, workspace: Path, sessions: SessionManager) -> None:
        """Update workspace/session bindings after a runtime workspace switch."""
        self.workspace = workspace
        self.sessions = sessions
        self._stores.clear()
        self._archive_stores.clear()

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared consolidation lock for one session."""
        return self._locks.setdefault(session_key, asyncio.Lock())

    async def consolidate_messages(self, messages: list[dict[str, object]]) -> bool:
        """Archive a selected message chunk into persistent memory."""
        session = self._active_session.get()
        store = self._get_store(session) if session is not None else self._get_default_store()
        return await store.consolidate(
            messages,
            self.provider,
            self.model,
            on_archive=self._archive_callbacks.get(),
        )

    def _make_archive_callback(
        self,
        session: Session,
        messages: list[dict[str, object]],
        *,
        source: str,
        extra_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Callable[[dict[str, Any]], None]:
        """Build the sidecar archive writer for one chunk."""

        def _callback(payload: dict[str, Any]) -> None:
            self._get_archive_store(session).write_archive(
                session_key=session.key,
                messages=messages,
                history_entry=str(payload.get("history_entry", "")).strip(),
                source=source,
                raw_archive=bool(payload.get("raw_archive")),
            )
            if extra_callback is not None:
                try:
                    extra_callback(payload)
                except Exception:
                    logger.exception("Extra archive callback failed")

        return _callback

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens."""
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        """Estimate current prompt size for the normal session history view."""
        history = session.get_history(max_messages=0, include_timestamps=True)
        channel, chat_id = session.key.split(":", 1) if ":" in session.key else (None, None)
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
            persona=self._get_persona(session),
            language=self._get_language(session),
        )
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    async def _archive_messages_locked(
        self,
        session: Session,
        messages: list[dict[str, object]],
        *,
        source: str,
        on_archive: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        """Archive messages with guaranteed persistence (retries until raw-dump fallback)."""
        if not messages:
            return False
        token = self._active_session.set(session)
        cb_token = self._archive_callbacks.set(
            self._make_archive_callback(
                session,
                messages,
                source=source,
                extra_callback=on_archive,
            )
        )
        try:
            return await self.archive(messages)
        finally:
            self._archive_callbacks.reset(cb_token)
            self._active_session.reset(token)

    async def archive(self, messages: list[dict[str, object]]) -> bool:
        """Backward-compatible archive entry point used by older tests and commands."""
        if not messages:
            return False

        session = self._active_session.get()
        if session is None:
            store = self._get_default_store()
            for _ in range(store._MAX_FAILURES_BEFORE_RAW_ARCHIVE):
                if await store.consolidate(messages, self.provider, self.model):
                    return True
            return True

        store = self._get_store(session)
        for _ in range(store._MAX_FAILURES_BEFORE_RAW_ARCHIVE):
            if await self.consolidate_messages(messages):
                return True
        return True

    async def archive_messages(
        self,
        session: Session,
        messages: list[dict[str, object]],
        *,
        source: str = "session_archive",
        on_archive: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        """Archive messages in the background with session-scoped memory persistence."""
        lock = self.get_lock(session.key)
        async with lock:
            return await self._archive_messages_locked(
                session,
                messages,
                source=source,
                on_archive=on_archive,
            )

    async def archive_unconsolidated(
        self,
        session: Session,
        *,
        source: str = "persona_switch",
        on_archive: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        """Archive the full unconsolidated tail for persona switch and similar rollover flows."""
        lock = self.get_lock(session.key)
        async with lock:
            snapshot = session.messages[session.last_consolidated :]
            if not snapshot:
                return True
            return await self._archive_messages_locked(
                session,
                snapshot,
                source=source,
                on_archive=on_archive,
            )

    async def maybe_consolidate_by_tokens(self, session: Session) -> None:
        """Loop: archive old messages until prompt fits within safe budget.

        The budget reserves space for completion tokens and a safety buffer
        so the LLM request never exceeds the context window.
        """
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            # Refresh session reference: AutoCompact may have truncated it while we waited for the lock.
            session = self.sessions.get_or_create(session.key)
            if not session.messages:
                return
            budget = self.context_window_tokens - self.max_completion_tokens - self._SAFETY_BUFFER
            target = budget // 2
            estimated, source = self.estimate_session_prompt_tokens(session)
            if estimated <= 0:
                return
            if estimated < budget:
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                )
                return

            for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                if estimated <= target:
                    return

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    return

                end_idx = boundary[0]
                chunk = session.messages[session.last_consolidated : end_idx]
                if not chunk:
                    return

                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                token = self._active_session.set(session)
                cb_token = self._archive_callbacks.set(
                    self._make_archive_callback(session, chunk, source="token_consolidation")
                )
                try:
                    if not await self.archive(chunk):
                        return
                finally:
                    self._archive_callbacks.reset(cb_token)
                    self._active_session.reset(token)
                session.last_consolidated = end_idx
                self.sessions.save(session)

                estimated, source = self.estimate_session_prompt_tokens(session)
                if estimated <= 0:
                    return


MemoryConsolidator = Consolidator


# ---------------------------------------------------------------------------
# Dream — heavyweight cron-scheduled memory consolidation
# ---------------------------------------------------------------------------


class Dream:
    """Two-phase memory processor: analyze history.jsonl, then update files via AgentRunner.

    Phase 1 produces an analysis summary (plain LLM call).
    Phase 2 delegates to AgentRunner with read_file / edit_file / write_file
    tools so the LLM can make targeted, incremental edits and create optional
    profile / insight layers without replacing entire files.
    """

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        max_batch_size: int = 20,
        max_iterations: int = 10,
        max_tool_result_chars: int = 16_000,
    ):
        self.store = store
        self.provider = provider
        self.model = model
        self.max_batch_size = max_batch_size
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars
        self._runner = AgentRunner(provider)
        self._tools = self._build_tools()

    # -- tool registry -------------------------------------------------------

    def _build_tools(self) -> ToolRegistry:
        """Build a minimal tool registry for the Dream agent."""
        from hahobot.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool

        tools = ToolRegistry()
        workspace = self.store.workspace
        tools.register(ReadFileTool(workspace=workspace, allowed_dir=workspace))
        tools.register(EditFileTool(workspace=workspace, allowed_dir=workspace))
        tools.register(WriteFileTool(workspace=workspace, allowed_dir=workspace))
        return tools

    # -- main entry ----------------------------------------------------------

    async def run(self) -> bool:
        """Process unprocessed history entries. Returns True if work was done."""
        last_cursor = self.store.get_last_dream_cursor()
        entries = self.store.read_unprocessed_history(since_cursor=last_cursor)
        if not entries:
            return False

        batch = entries[: self.max_batch_size]
        logger.info(
            "Dream: processing {} entries (cursor {}→{}), batch={}",
            len(entries),
            last_cursor,
            batch[-1]["cursor"],
            len(batch),
        )

        # Build history text for LLM
        history_text = "\n".join(f"[{e['timestamp']}] {e['content']}" for e in batch)

        # Current file contents
        current_date = datetime.now().strftime("%Y-%m-%d")
        current_memory = self.store.read_memory() or "(empty)"
        current_soul = self.store.read_soul() or "(empty)"
        current_user = self.store.read_user() or "(empty)"
        current_profile = self.store.read_profile() or "(empty)"
        current_insights = self.store.read_insights() or "(empty)"
        profile_metadata = format_memory_metadata_summary(
            "" if current_profile == "(empty)" else current_profile
        )
        insights_metadata = format_memory_metadata_summary(
            "" if current_insights == "(empty)" else current_insights
        )
        file_context = (
            f"## Current Date\n{current_date}\n\n"
            f"## Current MEMORY.md ({len(current_memory)} chars)\n{current_memory}\n\n"
            f"## Current SOUL.md ({len(current_soul)} chars)\n{current_soul}\n\n"
            f"## Current USER.md ({len(current_user)} chars)\n{current_user}\n\n"
            f"## Current PROFILE.md ({len(current_profile)} chars)\n{current_profile}\n\n"
            f"## PROFILE.md Metadata Summary\n{profile_metadata}\n\n"
            f"## Current INSIGHTS.md ({len(current_insights)} chars)\n{current_insights}\n\n"
            f"## INSIGHTS.md Metadata Summary\n{insights_metadata}"
        )

        # Phase 1: Analyze
        phase1_prompt = f"## Conversation History\n{history_text}\n\n{file_context}"

        try:
            phase1_response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": render_template("agent/dream_phase1.md", strip=True),
                    },
                    {"role": "user", "content": phase1_prompt},
                ],
                tools=None,
                tool_choice=None,
            )
            analysis = phase1_response.content or ""
            logger.debug("Dream Phase 1 analysis ({} chars): {}", len(analysis), analysis[:500])
        except Exception:
            logger.exception("Dream Phase 1 failed")
            return False

        # Phase 2: Delegate to AgentRunner with read_file / edit_file / write_file
        phase2_prompt = f"## Analysis Result\n{analysis}\n\n{file_context}"

        tools = self._tools
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": render_template("agent/dream_phase2.md", strip=True),
            },
            {"role": "user", "content": phase2_prompt},
        ]

        try:
            result = await self._runner.run(
                AgentRunSpec(
                    initial_messages=messages,
                    tools=tools,
                    model=self.model,
                    max_iterations=self.max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    fail_on_tool_error=False,
                )
            )
            logger.debug(
                "Dream Phase 2 complete: stop_reason={}, tool_events={}",
                result.stop_reason,
                len(result.tool_events),
            )
            for ev in result.tool_events or []:
                logger.info(
                    "Dream tool_event: name={}, status={}, detail={}",
                    ev.get("name"),
                    ev.get("status"),
                    ev.get("detail", "")[:200],
                )
        except Exception:
            logger.exception("Dream Phase 2 failed")
            result = None

        # Build changelog from tool events
        changelog: list[str] = []
        if result and result.tool_events:
            for event in result.tool_events:
                if event["status"] == "ok":
                    changelog.append(f"{event['name']}: {event['detail']}")

        # Advance cursor — always, to avoid re-processing Phase 1
        new_cursor = batch[-1]["cursor"]
        self.store.set_last_dream_cursor(new_cursor)
        self.store.compact_history()

        if result and result.stop_reason == "completed":
            logger.info(
                "Dream done: {} change(s), cursor advanced to {}",
                len(changelog),
                new_cursor,
            )
        else:
            reason = result.stop_reason if result else "exception"
            logger.warning(
                "Dream incomplete ({}): cursor advanced to {}",
                reason,
                new_cursor,
            )

        # Git auto-commit (only when there are actual changes)
        if changelog and self.store.git.is_initialized():
            ts = batch[-1]["timestamp"]
            sha = self.store.git.auto_commit(f"dream: {ts}, {len(changelog)} change(s)")
            if sha:
                logger.info("Dream commit: {}", sha)

        return True
