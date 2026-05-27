"""Derived SQLite/FTS5 index over MEMORY.md fragments.

The persona-local markdown file remains the source of truth. This module builds
a cache that lets a backend do top-K BM25 retrieval over individual fragments
instead of dumping the whole file into the prompt. The cache can always be
rebuilt from the markdown file.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

_HEADER_RE = re.compile(r"^<!--\s*(?P<body>.*?)\s*-->\s*$")
_TOKEN_RE = re.compile(r"(?P<key>ts|tag|src):(?P<value>\S+)")
_LEGACY_TAG = "legacy"
_UNKNOWN_SRC = "unknown"


def parse_memory_fragments(text: str, *, default_ts: str) -> list[dict[str, Any]]:
    """Split MEMORY.md *text* by blank-line boundaries, returning fragment dicts.

    Each dict has keys: id, fragment, ts, tag, src, fragment_order, char_len.

    ``id`` is ``sha256(fragment_body)[:16]`` (lowercase hex). Whitespace-only
    sections are skipped. Headers like
    ``<!-- ts:YYYY-MM-DDTHH:MM tag:WORD src:WORD -->`` on the first line are
    parsed and stripped from the body; remaining body becomes ``fragment``.
    Tokens may appear in any order; missing tokens default to
    ``tag=legacy``/``src=unknown``/``ts=default_ts``.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    sections = [block for block in re.split(r"\n\s*\n", normalized) if block.strip()]

    fragments: list[dict[str, Any]] = []
    for order, raw in enumerate(sections):
        ts, tag, src, body = _split_header(raw, default_ts=default_ts)
        body = body.strip()
        if not body:
            continue
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        fragments.append(
            {
                "id": digest,
                "fragment": body,
                "ts": ts,
                "tag": tag,
                "src": src,
                "fragment_order": order,
                "char_len": len(body),
            }
        )
    return fragments


def _split_header(raw: str, *, default_ts: str) -> tuple[str, str, str, str]:
    """Extract metadata header (if any) from the first line of *raw*."""
    first, _, remainder = raw.partition("\n")
    match = _HEADER_RE.match(first.strip())
    if not match:
        return default_ts, _LEGACY_TAG, _UNKNOWN_SRC, raw
    tokens = {m.group("key"): m.group("value") for m in _TOKEN_RE.finditer(match.group("body"))}
    if not tokens:
        return default_ts, _LEGACY_TAG, _UNKNOWN_SRC, raw
    return (
        tokens.get("ts", default_ts),
        tokens.get("tag", _LEGACY_TAG),
        tokens.get("src", _UNKNOWN_SRC),
        remainder,
    )


class MemoryFactsSQLiteIndex:
    """Persona-local derived FTS index over MEMORY.md fragments."""

    DB_FILENAME = "facts.sqlite"

    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = memory_dir
        self.db_path = memory_dir / self.DB_FILENAME

    def rebuild(self, fragments: list[dict[str, Any]], *, source_mtime_ns: int) -> int:
        """Wipe and recreate the index from *fragments*. Returns count inserted."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            self._drop_schema(conn)
            self._create_schema(conn)
            for fragment in fragments:
                self._insert_fragment(conn, fragment, source_mtime_ns=source_mtime_ns)
            conn.commit()
        return len(fragments)

    def search(
        self,
        *,
        query: str,
        limit: int,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search the FTS index. BM25 sort. Empty query falls through to recent."""
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)
        if not query.strip():
            return self.recent(limit=limit, tag=tag)

        where: list[str] = ["facts_fts MATCH ?"]
        params: list[Any] = [self._fts_query(query)]
        if tag:
            where.append("f.tag = ?")
            params.append(tag)
        params.append(max(1, min(limit, 50)))

        sql = f"""
            SELECT f.id, f.fragment, f.ts, f.tag, f.src, f.fragment_order, f.char_len
            FROM facts_fts
            JOIN facts f ON facts_fts.rowid = f.rowid
            WHERE {" AND ".join(where)}
            ORDER BY bm25(facts_fts), f.ts DESC
            LIMIT ?
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def recent(self, *, limit: int, tag: str | None = None) -> list[dict[str, Any]]:
        """Return up to *limit* fragments ordered by ts DESC, fragment_order DESC."""
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)

        where_sql = "1=1"
        params: list[Any] = []
        if tag:
            where_sql = "tag = ?"
            params.append(tag)
        params.append(max(1, min(limit, 50)))

        sql = f"""
            SELECT id, fragment, ts, tag, src, fragment_order, char_len
            FROM facts
            WHERE {where_sql}
            ORDER BY ts DESC, fragment_order DESC
            LIMIT ?
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def ensure_current(
        self,
        fragments: list[dict[str, Any]],
        *,
        source_mtime_ns: int,
    ) -> None:
        """Rebuild when DB is missing or older than the source mtime."""
        try:
            db_mtime_ns = self.db_path.stat().st_mtime_ns
        except FileNotFoundError:
            self.rebuild(fragments, source_mtime_ns=source_mtime_ns)
            return
        if db_mtime_ns < source_mtime_ns:
            self.rebuild(fragments, source_mtime_ns=source_mtime_ns)

    def _drop_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE IF EXISTS facts_fts")
        conn.execute("DROP TABLE IF EXISTS facts")

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                fragment TEXT NOT NULL,
                ts TEXT NOT NULL,
                tag TEXT NOT NULL,
                src TEXT NOT NULL,
                fragment_order INTEGER NOT NULL,
                char_len INTEGER NOT NULL,
                source_mtime_ns INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                id UNINDEXED,
                fragment,
                content=''
            )
            """
        )

    def _insert_fragment(
        self,
        conn: sqlite3.Connection,
        fragment: dict[str, Any],
        *,
        source_mtime_ns: int,
    ) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO facts(
                id, fragment, ts, tag, src, fragment_order, char_len, source_mtime_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fragment["id"],
                fragment["fragment"],
                fragment["ts"],
                fragment["tag"],
                fragment["src"],
                int(fragment["fragment_order"]),
                int(fragment["char_len"]),
                source_mtime_ns,
            ),
        )
        rowid = conn.execute(
            "SELECT rowid FROM facts WHERE id = ?",
            (fragment["id"],),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO facts_fts(rowid, id, fragment) VALUES (?, ?, ?)",
            (rowid, fragment["id"], fragment["fragment"]),
        )

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = [token.replace('"', '""') for token in query.split() if token.strip()]
        if not tokens:
            return '""'
        return " OR ".join(f'"{token}"' for token in tokens)
