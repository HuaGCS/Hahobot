"""Auto compact idle sessions into archived summaries plus a fresh live suffix."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

from hahobot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from hahobot.agent.memory import Consolidator


class AutoCompact:
    """Archive idle session tails and inject a one-shot summary on resume."""

    _RECENT_SUFFIX_MESSAGES = 8

    def __init__(
        self,
        sessions: SessionManager,
        consolidator: Consolidator,
        session_ttl_minutes: int = 0,
    ) -> None:
        self.sessions = sessions
        self.consolidator = consolidator
        self._ttl = max(0, int(session_ttl_minutes))
        self._archiving: set[str] = set()
        self._summaries: dict[str, tuple[str, datetime]] = {}

    def set_session_ttl_minutes(self, minutes: int) -> None:
        """Update the idle compact threshold for future checks."""
        self._ttl = max(0, int(minutes))

    def _is_expired(self, ts: datetime | str | None) -> bool:
        if self._ttl <= 0 or not ts:
            return False
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return (datetime.now() - ts).total_seconds() >= self._ttl * 60

    @staticmethod
    def _format_summary(text: str, last_active: datetime) -> str:
        idle_min = max(0, int((datetime.now() - last_active).total_seconds() / 60))
        return f"Inactive for {idle_min} minutes.\nPrevious conversation summary: {text}"

    def _split_unconsolidated(
        self,
        session: Session,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split the live tail into archiveable history and a retained legal suffix."""
        tail = list(session.messages[session.last_consolidated :])
        if not tail:
            return [], []

        probe = Session(
            key=session.key,
            messages=tail.copy(),
            created_at=session.created_at,
            updated_at=session.updated_at,
            metadata={},
            last_consolidated=0,
        )
        probe.retain_recent_legal_suffix(self._RECENT_SUFFIX_MESSAGES)
        kept = probe.messages
        cut = len(tail) - len(kept)
        return tail[:cut], kept

    def check_expired(self, schedule_background: Callable[[Coroutine[Any, Any, Any]], None]) -> None:
        """Schedule background archival for expired sessions."""
        if self._ttl <= 0:
            return
        for info in self.sessions.list_sessions():
            key = str(info.get("key") or "")
            if key and key not in self._archiving and self._is_expired(info.get("updated_at")):
                self._archiving.add(key)
                logger.debug(
                    "Auto-compact: scheduling archival for {} (idle > {} min)",
                    key,
                    self._ttl,
                )
                schedule_background(self._archive(key))

    async def _archive(self, key: str) -> None:
        """Archive the stale live prefix of one session and retain a fresh suffix."""
        try:
            self.sessions.invalidate(key)
            session = self.sessions.get_or_create(key)
            archive_msgs, kept_msgs = self._split_unconsolidated(session)
            if not archive_msgs and not kept_msgs:
                logger.debug("Auto-compact: skipping {}, no unconsolidated messages", key)
                session.updated_at = datetime.now()
                self.sessions.save(session)
                return

            last_active = session.updated_at
            archived_payload: dict[str, Any] = {}

            def _capture(payload: dict[str, Any]) -> None:
                archived_payload.update(payload)

            if archive_msgs:
                await self.consolidator.archive_messages(
                    session,
                    archive_msgs,
                    source="idle_auto_compact",
                    on_archive=_capture,
                )

            summary = str(archived_payload.get("history_entry") or "").strip()
            if summary:
                self._summaries[key] = (summary, last_active)
                session.metadata["_last_summary"] = {
                    "text": summary,
                    "last_active": last_active.isoformat(),
                }
            else:
                session.metadata.pop("_last_summary", None)

            session.messages = kept_msgs
            session.last_consolidated = 0
            session.updated_at = datetime.now()
            self.sessions.save(session)
            logger.info(
                "Auto-compact: archived {} (archived={}, kept={}, summary={})",
                key,
                len(archive_msgs),
                len(kept_msgs),
                bool(summary),
            )
        except Exception:
            logger.exception("Auto-compact: failed for {}", key)
        finally:
            self._archiving.discard(key)

    def prepare_session(self, session: Session, key: str) -> tuple[Session, str | None]:
        """Reload a session if needed and surface any pending one-shot resume summary."""
        if key in self._archiving or self._is_expired(session.updated_at):
            logger.info("Auto-compact: reloading session {} (archiving={})", key, key in self._archiving)
            session = self.sessions.get_or_create(key)

        entry = self._summaries.pop(key, None)
        if entry:
            session.metadata.pop("_last_summary", None)
            return session, self._format_summary(entry[0], entry[1])

        if "_last_summary" in session.metadata:
            meta = session.metadata.pop("_last_summary")
            self.sessions.save(session)
            return session, self._format_summary(
                str(meta["text"]),
                datetime.fromisoformat(str(meta["last_active"])),
            )

        return session, None
