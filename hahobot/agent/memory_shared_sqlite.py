"""Durable SQLite state for the optional cross-device shared-memory layer."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger

_CLAIM_LEASE_SECONDS = 120.0
_CONNECT_TIMEOUT_SECONDS = 5.0
_WAL_RETRY_INITIAL_SECONDS = 0.005
_WAL_RETRY_MAX_SECONDS = 0.05


def _is_sqlite_lock_error(exc: sqlite3.OperationalError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    message = str(exc).casefold()
    return "locked" in message or "busy" in message


def _ensure_wal_mode(conn: sqlite3.Connection) -> None:
    """Enable WAL with bounded retries for concurrent first-use lock upgrades."""
    deadline = time.monotonic() + _CONNECT_TIMEOUT_SECONDS
    delay = _WAL_RETRY_INITIAL_SECONDS
    last_error: sqlite3.OperationalError | None = None

    while True:
        try:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()
            if journal_mode is not None and str(journal_mode[0]).casefold() == "wal":
                return
            journal_mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            if journal_mode is not None and str(journal_mode[0]).casefold() == "wal":
                return
            last_error = None
        except sqlite3.OperationalError as exc:
            if not _is_sqlite_lock_error(exc):
                raise
            last_error = exc

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if last_error is not None:
                raise last_error
            raise sqlite3.OperationalError("failed to enable SQLite WAL journal mode")
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, _WAL_RETRY_MAX_SECONDS)


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield one transactional connection and always close its file descriptor."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        db_path.parent.chmod(0o700)
    except OSError:
        pass
    conn = sqlite3.connect(db_path, timeout=_CONNECT_TIMEOUT_SECONDS)
    try:
        conn.row_factory = sqlite3.Row
        # journal_mode lock upgrades can return SQLITE_BUSY immediately when two
        # fresh connections both hold a read lock, so busy_timeout alone is not
        # sufficient. Re-read the mode with bounded backoff until one wins.
        conn.execute(f"PRAGMA busy_timeout={int(_CONNECT_TIMEOUT_SECONDS * 1_000)}")
        _ensure_wal_mode(conn)
        # The snapshot is rebuildable, but the same database also owns the write
        # outbox. FULL keeps an acknowledged local enqueue durable across power loss.
        conn.execute("PRAGMA synchronous=FULL")
        try:
            db_path.chmod(0o600)
        except OSError:
            pass
        with conn:
            yield conn
    finally:
        conn.close()


class SharedMemorySQLiteState:
    """Cross-process-safe outbox and rebuildable remote-memory snapshot."""

    DB_FILENAME = "shared.sqlite"

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.db_path = state_dir / self.DB_FILENAME

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                messages_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                claim_token TEXT,
                claimed_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_outbox_due
                ON outbox(next_attempt_at, claimed_at);

            CREATE TABLE IF NOT EXISTS snapshot (
                memory_id TEXT PRIMARY KEY,
                memory TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                observed_at REAL NOT NULL,
                full_generation TEXT
            );

            CREATE TABLE IF NOT EXISTS shared_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

    def enqueue(self, event: dict[str, Any]) -> None:
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO outbox(
                    event_id, created_at, messages_json, metadata_json,
                    attempts, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event["id"]),
                    str(event["created_at"]),
                    json.dumps(event.get("messages") or [], ensure_ascii=False),
                    json.dumps(event.get("metadata") or {}, ensure_ascii=False),
                    int(event.get("attempts", 0) or 0),
                    float(event.get("next_attempt_at", 0) or 0),
                ),
            )

    def claim_due(
        self,
        *,
        force: bool,
        limit: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        token = uuid.uuid4().hex
        now = time.time()
        stale_before = now - _CLAIM_LEASE_SECONDS
        events: list[dict[str, Any]] = []
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            where = "(claim_token IS NULL OR claimed_at < ?)"
            params: list[Any] = [stale_before]
            if not force:
                where += " AND next_attempt_at <= ?"
                params.append(now)
            # Look past a handful of corrupt rows so one externally damaged
            # record cannot prevent healthy events from draining forever.
            params.append(max(25, max(1, limit) * 4))
            rows = conn.execute(
                f"""
                SELECT event_id, created_at, messages_json, metadata_json,
                       attempts, next_attempt_at
                FROM outbox
                WHERE {where}
                ORDER BY created_at, event_id
                LIMIT ?
                """,
                params,
            ).fetchall()
            valid_rows: list[sqlite3.Row] = []
            for row in rows:
                if len(valid_rows) >= max(1, limit):
                    break
                try:
                    messages = json.loads(row["messages_json"])
                    metadata = json.loads(row["metadata_json"])
                    if not isinstance(messages, list) or not isinstance(metadata, dict):
                        raise ValueError("unexpected outbox JSON shape")
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    attempts = int(row["attempts"]) + 1
                    conn.execute(
                        """
                        UPDATE outbox
                        SET attempts = ?, next_attempt_at = ?, claim_token = NULL, claimed_at = NULL
                        WHERE event_id = ?
                        """,
                        (attempts, now + 300.0, str(row["event_id"])),
                    )
                    logger.warning(
                        "Skipping malformed shared-memory outbox event {}: {}",
                        row["event_id"],
                        exc,
                    )
                    continue
                valid_rows.append(row)
                events.append(
                    {
                        "id": str(row["event_id"]),
                        "created_at": str(row["created_at"]),
                        "messages": messages,
                        "metadata": metadata,
                        "attempts": int(row["attempts"]),
                        "next_attempt_at": float(row["next_attempt_at"]),
                    }
                )
            if valid_rows:
                conn.executemany(
                    "UPDATE outbox SET claim_token = ?, claimed_at = ? WHERE event_id = ?",
                    [(token, now, str(row["event_id"])) for row in valid_rows],
                )
            conn.commit()
        return token, events

    def finish_claim(
        self,
        token: str,
        *,
        succeeded: set[str],
        failed: dict[str, tuple[int, float]],
    ) -> None:
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            if succeeded:
                conn.executemany(
                    "DELETE FROM outbox WHERE event_id = ? AND claim_token = ?",
                    [(event_id, token) for event_id in succeeded],
                )
            for event_id, (attempts, next_attempt_at) in failed.items():
                conn.execute(
                    """
                    UPDATE outbox
                    SET attempts = ?, next_attempt_at = ?, claim_token = NULL, claimed_at = NULL
                    WHERE event_id = ? AND claim_token = ?
                    """,
                    (attempts, next_attempt_at, event_id, token),
                )
            conn.execute(
                "UPDATE outbox SET claim_token = NULL, claimed_at = NULL WHERE claim_token = ?",
                (token,),
            )
            conn.commit()

    def release_claim(self, token: str) -> None:
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            conn.execute(
                "UPDATE outbox SET claim_token = NULL, claimed_at = NULL WHERE claim_token = ?",
                (token,),
            )

    def next_retry_delay(self) -> float | None:
        now = time.time()
        stale_before = now - _CLAIM_LEASE_SECONDS
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT MIN(
                    CASE
                        WHEN claim_token IS NOT NULL AND claimed_at >= ?
                            THEN claimed_at + ?
                        ELSE next_attempt_at
                    END
                ) AS due_at
                FROM outbox
                """,
                (stale_before, _CLAIM_LEASE_SECONDS),
            ).fetchone()
        if row is None or row["due_at"] is None:
            return None
        return max(0.0, float(row["due_at"]) - now)

    def pending_events(self) -> list[dict[str, Any]]:
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT event_id, created_at, messages_json, metadata_json,
                       attempts, next_attempt_at
                FROM outbox
                ORDER BY created_at, event_id
                """
            ).fetchall()
        return [
            {
                "id": str(row["event_id"]),
                "created_at": str(row["created_at"]),
                "messages": json.loads(row["messages_json"]),
                "metadata": json.loads(row["metadata_json"]),
                "attempts": int(row["attempts"]),
                "next_attempt_at": float(row["next_attempt_at"]),
            }
            for row in rows
        ]

    def merge_snapshot(self, items: list[dict[str, Any]]) -> None:
        observed_at = time.time()
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            conn.executemany(
                """
                INSERT INTO snapshot(
                    memory_id, memory, created_at, updated_at, observed_at, full_generation
                ) VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(memory_id) DO UPDATE SET
                    memory = excluded.memory,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    observed_at = excluded.observed_at
                """,
                [
                    (
                        item["id"],
                        item["memory"],
                        item.get("created_at"),
                        item.get("updated_at"),
                        observed_at,
                    )
                    for item in items
                ],
            )

    def claim_snapshot_refresh(self, refresh_seconds: int) -> tuple[str, float] | None:
        if refresh_seconds <= 0:
            return None
        now = time.time()
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            values = {
                row["key"]: row["value"]
                for row in conn.execute(
                    "SELECT key, value FROM shared_state WHERE key IN (?, ?, ?)",
                    (
                        "snapshot_last_full_refresh",
                        "snapshot_claim_token",
                        "snapshot_claimed_at",
                    ),
                ).fetchall()
            }
            last_refresh = float(values.get("snapshot_last_full_refresh", 0) or 0)
            claim_token = values.get("snapshot_claim_token", "")
            claimed_at = float(values.get("snapshot_claimed_at", 0) or 0)
            if now - last_refresh < refresh_seconds:
                conn.commit()
                return None
            if claim_token and now - claimed_at < _CLAIM_LEASE_SECONDS:
                conn.commit()
                return None

            token = uuid.uuid4().hex
            self._set_state(conn, "snapshot_claim_token", token)
            self._set_state(conn, "snapshot_claimed_at", str(now))
            conn.commit()
            return token, now

    def complete_snapshot_refresh(
        self,
        token: str,
        refresh_started_at: float,
        items: list[dict[str, Any]],
        *,
        prune_missing: bool = True,
    ) -> bool:
        now = time.time()
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            current = self._get_state(conn, "snapshot_claim_token")
            if current != token:
                conn.commit()
                return False
            conn.executemany(
                """
                INSERT INTO snapshot(
                    memory_id, memory, created_at, updated_at, observed_at, full_generation
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    memory = excluded.memory,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    observed_at = excluded.observed_at,
                    full_generation = excluded.full_generation
                WHERE snapshot.observed_at <= ?
                """,
                [
                    (
                        item["id"],
                        item["memory"],
                        item.get("created_at"),
                        item.get("updated_at"),
                        now,
                        token,
                        refresh_started_at,
                    )
                    for item in items
                ],
            )
            if prune_missing:
                conn.execute(
                    """
                    DELETE FROM snapshot
                    WHERE COALESCE(full_generation, '') != ? AND observed_at <= ?
                    """,
                    (token, refresh_started_at),
                )
            self._set_state(conn, "snapshot_last_full_refresh", str(now))
            self._set_state(conn, "snapshot_claim_token", "")
            self._set_state(conn, "snapshot_claimed_at", "0")
            conn.commit()
            return True

    def abort_snapshot_refresh(self, token: str) -> None:
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            if self._get_state(conn, "snapshot_claim_token") == token:
                self._set_state(conn, "snapshot_claim_token", "")
                self._set_state(conn, "snapshot_claimed_at", "0")
            conn.commit()

    def snapshot_items(self) -> list[dict[str, Any]]:
        with _connect(self.db_path) as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT memory_id, memory, created_at, updated_at
                FROM snapshot
                ORDER BY COALESCE(updated_at, created_at, '')
                """
            ).fetchall()
        return [
            {
                "id": str(row["memory_id"]),
                "memory": str(row["memory"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _get_state(conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute("SELECT value FROM shared_state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else ""

    @staticmethod
    def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO shared_state(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
