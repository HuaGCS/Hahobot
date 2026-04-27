"""Session management for conversation history."""

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from hahobot.config.paths import get_legacy_sessions_dir
from hahobot.utils.helpers import ensure_dir, find_legal_message_start, safe_filename


@dataclass
class Session:
    """A conversation session."""

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files
    _persisted_message_count: int = field(default=0, init=False, repr=False)
    _persisted_metadata_state: str = field(default="", init=False, repr=False)
    _requires_full_save: bool = field(default=False, init=False, repr=False)

    @staticmethod
    def _annotate_message_time(message: dict[str, Any], content: Any) -> Any:
        """Expose persisted turn timestamps to the model for relative-date reasoning."""
        timestamp = message.get("timestamp")
        if (
            not timestamp
            or message.get("role") not in {"user", "assistant"}
            or not isinstance(content, str)
        ):
            return content
        return f"[Message Time: {timestamp}]\n{content}"

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(
        self,
        max_messages: int = 500,
        *,
        include_timestamps: bool = False,
    ) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a legal tool-call boundary."""
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        # Avoid starting mid-turn when possible.
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[i:]
                break

        # Drop orphan tool results at the front.
        start = find_legal_message_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            content = message.get("content", "")
            if include_timestamps:
                content = self._annotate_message_time(message, content)
            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()
        self._requires_full_save = True

    def retain_recent_legal_suffix(self, max_messages: int) -> None:
        """Keep a legal recent suffix, mirroring get_history boundary rules."""
        if max_messages <= 0:
            self.clear()
            return
        if len(self.messages) <= max_messages:
            return

        start_idx = max(0, len(self.messages) - max_messages)

        # If the cutoff lands mid-turn, extend backward to the nearest user turn.
        while start_idx > 0 and self.messages[start_idx].get("role") != "user":
            start_idx -= 1

        retained = self.messages[start_idx:]

        # Mirror get_history(): avoid persisting orphan tool results at the front.
        start = find_legal_message_start(retained)
        if start:
            retained = retained[start:]

        dropped = len(self.messages) - len(retained)
        self.messages = retained
        self.last_consolidated = max(0, self.last_consolidated - dropped)
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        self._cache: dict[str, Session] = {}

    def rebind_workspace(self, workspace: Path) -> None:
        """Repoint session storage to a new workspace root."""
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self._cache.clear()

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.hahobot/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages, metadata, created_at, updated_at, last_consolidated, _ = self._read_session_file(
                path
            )
            session = self._build_session(
                key,
                path,
                messages=messages,
                metadata=metadata,
                created_at=created_at,
                updated_at=updated_at,
                last_consolidated=last_consolidated,
            )
            self._mark_persisted(session)
            return session
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info(
                    "Recovered session {} from corrupt file ({} messages)",
                    key,
                    len(repaired.messages),
                )
            return repaired

    @staticmethod
    def _metadata_state(session: Session) -> str:
        """Serialize metadata fields that require a checkpoint line."""
        return json.dumps(
            {
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @staticmethod
    def _metadata_line(session: Session) -> dict[str, Any]:
        """Build a metadata checkpoint record."""
        return {
            "_type": "metadata",
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "last_consolidated": session.last_consolidated
        }

    @staticmethod
    def _write_jsonl_line(handle: Any, payload: dict[str, Any]) -> None:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _parse_iso_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _read_session_file(
        self,
        path: Path,
        *,
        tolerate_corrupt: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], datetime | None, datetime | None, int, int]:
        messages: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {}
        created_at: datetime | None = None
        updated_at: datetime | None = None
        last_consolidated = 0
        skipped = 0

        with open(path, encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    if not tolerate_corrupt:
                        raise
                    skipped += 1
                    continue

                if data.get("_type") == "metadata":
                    metadata = data.get("metadata", {})
                    created_at = self._parse_iso_datetime(data.get("created_at")) or created_at
                    updated_at = self._parse_iso_datetime(data.get("updated_at")) or updated_at
                    last_consolidated = data.get("last_consolidated", 0)
                else:
                    messages.append(data)

        return messages, metadata, created_at, updated_at, last_consolidated, skipped

    def _build_session(
        self,
        key: str,
        path: Path,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        created_at: datetime | None,
        updated_at: datetime | None,
        last_consolidated: int,
    ) -> Session:
        resolved_created_at = created_at or datetime.now()
        resolved_updated_at = updated_at or datetime.fromtimestamp(path.stat().st_mtime)
        if resolved_updated_at < resolved_created_at:
            resolved_updated_at = resolved_created_at

        return Session(
            key=key,
            messages=messages,
            created_at=resolved_created_at,
            updated_at=resolved_updated_at,
            metadata=metadata,
            last_consolidated=last_consolidated,
        )

    def _mark_persisted(self, session: Session) -> None:
        session._persisted_message_count = len(session.messages)
        session._persisted_metadata_state = self._metadata_state(session)
        session._requires_full_save = False

    def _rewrite_session_file(self, path: Path, session: Session) -> None:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                self._write_jsonl_line(f, self._metadata_line(session))
                for msg in session.messages:
                    self._write_jsonl_line(f, msg)
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        self._mark_persisted(session)

    def _repair(self, key: str) -> Session | None:
        """Recover valid lines from a partially written or corrupt JSONL session."""
        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            messages, metadata, created_at, updated_at, last_consolidated, skipped = (
                self._read_session_file(path, tolerate_corrupt=True)
            )
            if skipped:
                logger.warning("Skipped {} corrupt lines in session {}", skipped, key)
            if (
                not messages
                and not metadata
                and created_at is None
                and updated_at is None
                and last_consolidated == 0
            ):
                return None

            session = self._build_session(
                key,
                path,
                messages=messages,
                metadata=metadata,
                created_at=created_at,
                updated_at=updated_at,
                last_consolidated=last_consolidated,
            )
            session._requires_full_save = True
            return session
        except Exception as e:
            logger.warning("Repair failed for session {}: {}", key, e)
            return None

    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)
        metadata_state = self._metadata_state(session)
        needs_full_rewrite = (
            session._requires_full_save
            or not path.exists()
            or session._persisted_message_count > len(session.messages)
        )

        if needs_full_rewrite:
            session.updated_at = datetime.now()
            self._rewrite_session_file(path, session)
        else:
            new_messages = session.messages[session._persisted_message_count:]
            metadata_changed = metadata_state != session._persisted_metadata_state

            if new_messages or metadata_changed:
                session.updated_at = datetime.now()
                with open(path, "a", encoding="utf-8") as f:
                    for msg in new_messages:
                        self._write_jsonl_line(f, msg)
                    if metadata_changed:
                        self._write_jsonl_line(f, self._metadata_line(session))
                self._mark_persisted(session)

        self._cache[session.key] = session

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                created_at = None
                key = path.stem.replace("_", ":", 1)
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or key
                            created_at = data.get("created_at")

                # Incremental saves append messages without rewriting the first metadata line,
                # so use file mtime as the session's latest activity timestamp.
                sessions.append({
                    "key": key,
                    "created_at": created_at,
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                    "path": str(path)
                })
            except Exception:
                repaired = self._repair(path.stem.replace("_", ":", 1))
                if repaired is not None:
                    sessions.append({
                        "key": repaired.key,
                        "created_at": repaired.created_at.isoformat(),
                        "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                        "path": str(path),
                    })
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
