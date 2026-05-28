"""Tests for the SQLite-FTS user memory backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from hahobot.agent.memory_backends.sqlite_backend import SQLiteUserMemoryBackend
from hahobot.agent.memory_models import MemoryScope
from hahobot.agent.personas import persona_workspace


def _scope(workspace: Path, *, query: str | None = None, persona: str = "default") -> MemoryScope:
    return MemoryScope(
        workspace=workspace,
        session_key="cli:direct",
        channel="cli",
        chat_id="direct",
        sender_id="user",
        persona=persona,
        language="en",
        query=query,
    )


def _seed_memory(workspace: Path, persona: str, body: str) -> Path:
    persona_root = persona_workspace(workspace, persona)
    memory_dir = persona_root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_file = memory_dir / "MEMORY.md"
    memory_file.write_text(body, encoding="utf-8")
    return memory_file


@pytest.mark.asyncio
async def test_resolve_context_returns_empty_block_when_memory_missing(tmp_path: Path) -> None:
    backend = SQLiteUserMemoryBackend()
    resolved = await backend.resolve_context(_scope(tmp_path))
    assert resolved.block == ""
    assert resolved.source == "sqlite"


@pytest.mark.asyncio
async def test_resolve_context_returns_top_k_when_query_matches(tmp_path: Path) -> None:
    body = (
        "<!-- ts:2026-05-26T17:30 tag:preference src:turn -->\n"
        "User prefers concise replies and no trailing summary.\n"
        "\n"
        "<!-- ts:2026-05-26T18:00 tag:project src:dream -->\n"
        "Hahobot replaces Mem0 with a SQLite-FTS backed memory layer.\n"
        "\n"
        "<!-- ts:2026-05-26T18:30 tag:reference src:turn -->\n"
        "Unrelated topic about cat photos.\n"
    )
    _seed_memory(tmp_path, "default", body)

    backend = SQLiteUserMemoryBackend(top_k=5)
    resolved = await backend.resolve_context(_scope(tmp_path, query="SQLite memory"))
    assert resolved.source == "sqlite"
    assert "Hahobot replaces Mem0" in resolved.block
    assert "Long-term Memory" in resolved.block


@pytest.mark.asyncio
async def test_resolve_context_falls_back_to_recent_when_query_empty(tmp_path: Path) -> None:
    body = (
        "<!-- ts:2024-01-01T00:00 tag:legacy src:unknown -->\n"
        "older fact one\n"
        "\n"
        "<!-- ts:2026-05-26T18:00 tag:project src:dream -->\n"
        "newest fact two\n"
    )
    _seed_memory(tmp_path, "default", body)

    backend = SQLiteUserMemoryBackend(top_k=1)
    resolved = await backend.resolve_context(_scope(tmp_path, query=""))
    assert "newest fact two" in resolved.block
    assert "older fact one" not in resolved.block


@pytest.mark.asyncio
async def test_resolve_context_respects_max_context_chars(tmp_path: Path) -> None:
    body = "\n\n".join(
        f"<!-- ts:2026-05-26T17:{minute:02d} tag:project src:turn -->\n"
        f"fragment number {minute} with some padding text to consume space"
        for minute in range(10)
    )
    _seed_memory(tmp_path, "default", body)

    backend = SQLiteUserMemoryBackend(top_k=20, max_context_chars=200)
    resolved = await backend.resolve_context(_scope(tmp_path, query=""))
    assert len(resolved.block) <= 200 + 50  # heading line and final newline tolerance


@pytest.mark.asyncio
async def test_resolve_context_handles_legacy_only_memory(tmp_path: Path) -> None:
    body = "plain paragraph one.\n\nplain paragraph two.\n"
    _seed_memory(tmp_path, "default", body)

    backend = SQLiteUserMemoryBackend()
    resolved = await backend.resolve_context(_scope(tmp_path, query="paragraph"))
    assert "plain paragraph" in resolved.block


@pytest.mark.asyncio
async def test_resolve_context_reuses_index_across_calls(tmp_path: Path) -> None:
    body = "<!-- ts:2026-05-26T17:30 tag:preference src:turn -->\nalpha bravo charlie\n"
    memory_file = _seed_memory(tmp_path, "default", body)

    backend = SQLiteUserMemoryBackend()
    await backend.resolve_context(_scope(tmp_path, query="alpha"))
    index_path = memory_file.parent / "facts.sqlite"
    assert index_path.exists()
    first_mtime = index_path.stat().st_mtime_ns

    await backend.resolve_context(_scope(tmp_path, query="alpha"))
    assert index_path.stat().st_mtime_ns == first_mtime
