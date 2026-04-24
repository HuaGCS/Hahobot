"""Optional SQLite FTS derived index for structured history archives."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from loguru import logger


class HistoryArchiveSQLiteIndex:
    """Persona-local derived search index built from archive JSONL/chunks."""

    DB_FILENAME = "index.sqlite"

    def __init__(self, archive_dir: Path) -> None:
        self.archive_dir = archive_dir
        self.db_path = archive_dir / self.DB_FILENAME

    def rebuild(self, entries: list[dict[str, Any]]) -> int:
        """Rebuild the derived SQLite/FTS index from archive index entries."""
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            self._drop_schema(conn)
            self._create_schema(conn)
            for entry in entries:
                self._insert_entry(conn, entry)
            conn.commit()
        return len(entries)

    def search(
        self,
        *,
        query: str,
        limit: int,
        session_key: str | None = None,
        preferred_session_key: str | None = None,
        since: str | None = None,
        until: str | None = None,
        file: str | None = None,
        observation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search the derived index. Raises on SQLite errors so caller can fallback."""
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)

        where: list[str] = []
        params: list[Any] = []
        if query.strip():
            where.append("observations_fts MATCH ?")
            params.append(self._fts_query(query))
        if session_key:
            where.append("o.session_key = ?")
            params.append(session_key)
        if since:
            where.append("o.time_end >= ?")
            params.append(since)
        if until:
            where.append("o.time_end <= ?")
            params.append(until)
        if file:
            where.append("o.files_lc LIKE ?")
            params.append(f"%{file.lower()}%")
        if observation_type:
            where.append("o.observation_type = ?")
            params.append(observation_type.lower())

        where_sql = " AND ".join(where) if where else "1=1"
        preference_sql = (
            "CASE WHEN o.session_key = ? THEN 3 ELSE 0 END" if preferred_session_key else "0"
        )
        if preferred_session_key:
            params.append(preferred_session_key)
        rank_sql = "bm25(observations_fts) * -1" if query.strip() else "0"

        sql = f"""
            SELECT o.payload
            FROM observations_fts
            JOIN observations o ON observations_fts.rowid = o.rowid
            WHERE {where_sql}
            ORDER BY ({preference_sql}) + {rank_sql} DESC, o.time_end DESC
            LIMIT ?
        """
        params.append(max(1, min(limit, 20)))
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def ensure_current(self, entries: list[dict[str, Any]], *, index_mtime_ns: int) -> None:
        """Rebuild index when missing or older than the JSONL source."""
        try:
            db_mtime_ns = self.db_path.stat().st_mtime_ns
        except FileNotFoundError:
            self.rebuild(entries)
            return
        if db_mtime_ns < index_mtime_ns:
            self.rebuild(entries)

    def _drop_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE IF EXISTS observations_fts")
        conn.execute("DROP TABLE IF EXISTS observations")

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                time_start TEXT,
                time_end TEXT,
                observation_type TEXT,
                files_lc TEXT,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
                id UNINDEXED,
                title,
                summary,
                facts,
                concepts,
                files,
                keywords,
                tools,
                content=''
            )
            """
        )

    def _insert_entry(self, conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
        payload = json.dumps(entry, ensure_ascii=False)
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            return
        files = [str(item) for item in entry.get("files") or []]
        conn.execute(
            """
            INSERT OR REPLACE INTO observations(
                id, session_key, time_start, time_end, observation_type, files_lc, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                str(entry.get("sessionKey") or ""),
                str(entry.get("timeStart") or ""),
                str(entry.get("timeEnd") or ""),
                str(entry.get("observationType") or "").lower(),
                "\n".join(files).lower(),
                payload,
            ),
        )
        rowid = conn.execute(
            "SELECT rowid FROM observations WHERE id = ?", (entry_id,)
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO observations_fts(
                rowid, id, title, summary, facts, concepts, files, keywords, tools
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rowid,
                entry_id,
                str(entry.get("title") or ""),
                str(entry.get("summary") or ""),
                self._join(entry.get("facts")),
                self._join(entry.get("concepts")),
                self._join(entry.get("files")),
                self._join(entry.get("keywords")),
                self._join(entry.get("tools")),
            ),
        )

    @staticmethod
    def _join(value: Any) -> str:
        if isinstance(value, list):
            return "\n".join(str(item) for item in value if str(item).strip())
        return str(value or "")

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = [token.replace('"', '""') for token in query.split() if token.strip()]
        if not tokens:
            return '""'
        return " OR ".join(f'"{token}"' for token in tokens)


def try_rebuild_sqlite_index(archive_dir: Path, entries: list[dict[str, Any]]) -> int:
    """Best-effort helper for CLI/tests."""
    try:
        return HistoryArchiveSQLiteIndex(archive_dir).rebuild(entries)
    except sqlite3.Error:
        logger.exception("Failed to rebuild history archive SQLite index")
        raise
