"""SQLite-FTS backed user memory backend.

Reads MEMORY.md from the persona workspace (the source of truth), keeps a
derived FTS5 index in ``memory/facts.sqlite`` up to date by mtime, and resolves
prompt context by running a top-K BM25 search keyed by the current turn's
inbound text (or falling back to most-recent fragments when the scope has no
query).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from hahobot.agent.memory import MemoryStore
from hahobot.agent.memory_backends.base import UserMemoryBackend
from hahobot.agent.memory_facts_sqlite import (
    MemoryFactsSQLiteIndex,
    parse_memory_fragments,
)
from hahobot.agent.memory_models import MemoryScope, ResolvedMemoryContext
from hahobot.agent.personas import persona_workspace


class SQLiteUserMemoryBackend(UserMemoryBackend):
    """Retrieve user memory by BM25 over the persona MEMORY.md fragments."""

    _DEFAULT_TOP_K = 8
    _DEFAULT_MAX_CONTEXT_CHARS = 4_000
    _DEFAULT_MAX_FRAGMENT_CHARS = 500

    def __init__(
        self,
        *,
        top_k: int = _DEFAULT_TOP_K,
        max_context_chars: int = _DEFAULT_MAX_CONTEXT_CHARS,
        max_fragment_chars: int = _DEFAULT_MAX_FRAGMENT_CHARS,
    ) -> None:
        self._top_k = max(1, int(top_k))
        self._max_context_chars = max(0, int(max_context_chars))
        self._max_fragment_chars = max(0, int(max_fragment_chars))

    async def resolve_context(self, scope: MemoryScope) -> ResolvedMemoryContext:
        workspace = persona_workspace(scope.workspace, scope.persona)
        store = MemoryStore(workspace)
        memory_text = store.read_memory()
        if not memory_text.strip():
            return ResolvedMemoryContext(block="", source="sqlite")

        try:
            mtime_ns = store.memory_file.stat().st_mtime_ns
        except FileNotFoundError:
            return ResolvedMemoryContext(block="", source="sqlite")

        default_ts = datetime.fromtimestamp(mtime_ns / 1_000_000_000).strftime("%Y-%m-%dT%H:%M")
        fragments = parse_memory_fragments(memory_text, default_ts=default_ts)
        if not fragments:
            return ResolvedMemoryContext(block="", source="sqlite")

        index = MemoryFactsSQLiteIndex(store.memory_dir)
        try:
            index.ensure_current(fragments, source_mtime_ns=mtime_ns)
            query = (scope.query or "").strip()
            results = index.search(query=query, limit=self._top_k)
        except Exception:
            logger.exception("SQLite user memory index lookup failed; returning empty context")
            return ResolvedMemoryContext(block="", source="sqlite")

        block = self._format_block(results)
        return ResolvedMemoryContext(block=block, source="sqlite")

    def _format_block(self, results: list[dict[str, Any]]) -> str:
        if not results:
            return ""
        lines = ["## Long-term Memory"]
        total = len(lines[0])
        for row in results:
            fragment = row.get("fragment") or ""
            if not fragment.strip():
                continue
            if self._max_fragment_chars and len(fragment) > self._max_fragment_chars:
                fragment = fragment[: self._max_fragment_chars - 3].rstrip() + "..."
            line = f"- {fragment}"
            if self._max_context_chars and total + len(line) + 1 > self._max_context_chars:
                break
            lines.append(line)
            total += len(line) + 1
        return "\n".join(lines) if len(lines) > 1 else ""


def _persona_workspace_for(scope: MemoryScope) -> Path:
    """Expose persona resolution for tests and callers that don't want to import twice."""
    return persona_workspace(scope.workspace, scope.persona)
