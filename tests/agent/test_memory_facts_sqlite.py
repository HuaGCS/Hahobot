"""Tests for the MEMORY.md derived SQLite/FTS index."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from hahobot.agent.memory_facts_sqlite import (
    MemoryFactsSQLiteIndex,
    parse_memory_fragments,
)

DEFAULT_TS = "2000-01-01T00:00"


def test_parse_structured_fragments() -> None:
    text = (
        "<!-- ts:2026-05-26T17:30 tag:preference src:turn -->\n"
        "User prefers concise replies.\n"
        "\n"
        "<!-- src:dream tag:project ts:2026-05-26T18:00 -->\n"
        "Replace Mem0 with SQLite-FTS.\n"
        "Backend is sqlite, fallback is file.\n"
        "\n"
        "<!-- tag:reference src:turn ts:2026-05-20T09:00 -->\n"
        "Admin entry is in field_specs.py.\n"
    )
    fragments = parse_memory_fragments(text, default_ts=DEFAULT_TS)
    assert [f["ts"] for f in fragments] == [
        "2026-05-26T17:30",
        "2026-05-26T18:00",
        "2026-05-20T09:00",
    ]
    assert [f["tag"] for f in fragments] == ["preference", "project", "reference"]
    assert [f["src"] for f in fragments] == ["turn", "dream", "turn"]
    assert fragments[0]["fragment"] == "User prefers concise replies."
    assert fragments[1]["fragment"].startswith("Replace Mem0 with SQLite-FTS.")
    assert "<!--" not in fragments[0]["fragment"]


def test_parse_legacy_fragments_default_metadata() -> None:
    text = "first plain paragraph.\n\nsecond plain paragraph.\n"
    fragments = parse_memory_fragments(text, default_ts=DEFAULT_TS)
    assert len(fragments) == 2
    for fragment in fragments:
        assert fragment["tag"] == "legacy"
        assert fragment["src"] == "unknown"
        assert fragment["ts"] == DEFAULT_TS


def test_parse_mixed_fragments() -> None:
    text = (
        "<!-- ts:2026-05-26T17:30 tag:preference src:turn -->\nconcise replies\n\nno header here\n"
    )
    fragments = parse_memory_fragments(text, default_ts=DEFAULT_TS)
    assert fragments[0]["tag"] == "preference"
    assert fragments[1]["tag"] == "legacy"
    assert fragments[1]["src"] == "unknown"


def test_parse_skips_blank_sections() -> None:
    text = "\n\n\nfirst\n\n\n\nsecond\n\n\n"
    fragments = parse_memory_fragments(text, default_ts=DEFAULT_TS)
    assert [f["fragment"] for f in fragments] == ["first", "second"]


def test_id_is_stable_per_fragment_body() -> None:
    text_a = "<!-- ts:2026-05-26T17:30 tag:preference src:turn -->\nshared body"
    text_b = "<!-- ts:2026-05-27T00:00 tag:project src:dream -->\nshared body"
    a = parse_memory_fragments(text_a, default_ts=DEFAULT_TS)[0]
    b = parse_memory_fragments(text_b, default_ts=DEFAULT_TS)[0]
    assert a["id"] == b["id"]
    assert a["tag"] != b["tag"]


def _persona_workspace(tmp_path: Path) -> Path:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    return memory_dir


def _seed(index: MemoryFactsSQLiteIndex, fragments: list[dict]) -> None:
    index.rebuild(fragments, source_mtime_ns=time.time_ns())


def test_rebuild_and_search_returns_bm25_ordered(tmp_path: Path) -> None:
    memory_dir = _persona_workspace(tmp_path)
    index = MemoryFactsSQLiteIndex(memory_dir)
    fragments = parse_memory_fragments(
        "alpha appears once here\n\nalpha alpha alpha core hit\n\nunrelated content",
        default_ts="2026-05-26T17:00",
    )
    _seed(index, fragments)
    results = index.search(query="alpha", limit=5)
    assert results[0]["fragment"].startswith("alpha alpha alpha")
    assert len(results) == 2


def test_search_empty_query_returns_recent(tmp_path: Path) -> None:
    memory_dir = _persona_workspace(tmp_path)
    index = MemoryFactsSQLiteIndex(memory_dir)
    fragments = [
        {
            "id": "a",
            "fragment": "first",
            "ts": "2026-05-26T17:30",
            "tag": "preference",
            "src": "turn",
            "fragment_order": 0,
            "char_len": 5,
        },
        {
            "id": "b",
            "fragment": "second",
            "ts": "2026-05-26T18:00",
            "tag": "project",
            "src": "dream",
            "fragment_order": 1,
            "char_len": 6,
        },
    ]
    _seed(index, fragments)
    results = index.search(query="   ", limit=5)
    assert [r["id"] for r in results] == ["b", "a"]


def test_recent_orders_by_ts_desc(tmp_path: Path) -> None:
    memory_dir = _persona_workspace(tmp_path)
    index = MemoryFactsSQLiteIndex(memory_dir)
    fragments = [
        {
            "id": "old",
            "fragment": "older fact",
            "ts": "2024-01-01T00:00",
            "tag": "legacy",
            "src": "unknown",
            "fragment_order": 0,
            "char_len": 10,
        },
        {
            "id": "new",
            "fragment": "newer fact",
            "ts": "2026-05-26T18:00",
            "tag": "project",
            "src": "dream",
            "fragment_order": 1,
            "char_len": 10,
        },
    ]
    _seed(index, fragments)
    results = index.recent(limit=5)
    assert [r["id"] for r in results] == ["new", "old"]


def test_search_filters_by_tag(tmp_path: Path) -> None:
    memory_dir = _persona_workspace(tmp_path)
    index = MemoryFactsSQLiteIndex(memory_dir)
    fragments = [
        {
            "id": "p",
            "fragment": "alpha preference body",
            "ts": "2026-05-26T17:30",
            "tag": "preference",
            "src": "turn",
            "fragment_order": 0,
            "char_len": 21,
        },
        {
            "id": "j",
            "fragment": "alpha project body",
            "ts": "2026-05-26T18:00",
            "tag": "project",
            "src": "dream",
            "fragment_order": 1,
            "char_len": 18,
        },
    ]
    _seed(index, fragments)
    project_only = index.search(query="alpha", limit=10, tag="project")
    assert [r["id"] for r in project_only] == ["j"]


def test_ensure_current_rebuilds_when_db_missing(tmp_path: Path) -> None:
    memory_dir = _persona_workspace(tmp_path)
    index = MemoryFactsSQLiteIndex(memory_dir)
    fragments = parse_memory_fragments("hello world", default_ts="2026-05-26T17:00")
    index.ensure_current(fragments, source_mtime_ns=time.time_ns())
    assert index.db_path.exists()
    assert len(index.recent(limit=5)) == 1


def test_ensure_current_skips_when_up_to_date(tmp_path: Path) -> None:
    memory_dir = _persona_workspace(tmp_path)
    index = MemoryFactsSQLiteIndex(memory_dir)
    fragments = parse_memory_fragments("payload", default_ts="2026-05-26T17:00")
    mtime = time.time_ns()
    index.ensure_current(fragments, source_mtime_ns=mtime)

    with sqlite3.connect(index.db_path) as conn:
        conn.execute(
            "INSERT INTO facts(id, fragment, ts, tag, src, fragment_order, char_len, source_mtime_ns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("manual", "manual marker", "2026-05-26T18:00", "preference", "turn", 99, 13, mtime),
        )
        conn.commit()

    index.ensure_current(fragments, source_mtime_ns=mtime)
    ids = {row["id"] for row in index.recent(limit=10)}
    assert "manual" in ids


def test_ensure_current_rebuilds_when_source_is_newer(tmp_path: Path) -> None:
    memory_dir = _persona_workspace(tmp_path)
    index = MemoryFactsSQLiteIndex(memory_dir)
    base = parse_memory_fragments("first", default_ts="2026-05-26T17:00")
    base_mtime = time.time_ns()
    index.ensure_current(base, source_mtime_ns=base_mtime)

    newer = parse_memory_fragments("first\n\nsecond", default_ts="2026-05-26T17:00")
    index.ensure_current(newer, source_mtime_ns=base_mtime + 1_000_000_000)

    fragments = index.recent(limit=10)
    assert {f["fragment"] for f in fragments} == {"first", "second"}


def test_search_raises_when_db_missing(tmp_path: Path) -> None:
    memory_dir = _persona_workspace(tmp_path)
    index = MemoryFactsSQLiteIndex(memory_dir)
    with pytest.raises(FileNotFoundError):
        index.search(query="alpha", limit=5)
