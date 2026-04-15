"""Session metadata and memory-scope helpers for AgentLoop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from hahobot.agent.i18n import DEFAULT_LANGUAGE, resolve_language
from hahobot.agent.memory_models import MemoryCommitRequest, MemoryScope

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop, _SessionTurnState
    from hahobot.session.manager import Session


class SessionRuntimeManager:
    """Own session metadata helpers and memory-scope plumbing for AgentLoop."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    def get_session_persona(self, session: Session) -> str:
        """Return the active persona name for a session."""
        return self.loop.context.resolve_persona(session.metadata.get("persona"))

    def get_session_language(self, session: Session) -> str:
        """Return the active language for a session."""
        metadata = getattr(session, "metadata", {})
        raw = metadata.get("language") if isinstance(metadata, dict) else DEFAULT_LANGUAGE
        return resolve_language(raw)

    def set_session_persona(self, session: Session, persona: str) -> None:
        """Persist the selected persona for a session."""
        if persona == "default":
            session.metadata.pop("persona", None)
        else:
            session.metadata["persona"] = persona

    def set_session_language(self, session: Session, language: str) -> None:
        """Persist the selected language for a session."""
        if language == DEFAULT_LANGUAGE:
            session.metadata.pop("language", None)
        else:
            session.metadata["language"] = language

    def memory_scope(
        self,
        session: Session,
        *,
        channel: str,
        chat_id: str,
        sender_id: str | None,
        persona: str | None = None,
        language: str | None = None,
        query: str | None = None,
    ) -> MemoryScope:
        """Build the normalized scope used by memory backends."""
        return MemoryScope(
            workspace=self.loop.workspace,
            session_key=session.key,
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            persona=persona or self.get_session_persona(session),
            language=language or self.get_session_language(session),
            query=query,
        )

    async def commit_memory_turn(
        self,
        *,
        scope: MemoryScope,
        inbound_content: Any | None,
        outbound_content: str | None,
        persisted_messages: list[dict[str, Any]],
    ) -> None:
        """Forward a completed turn to the memory router without blocking replies on failures."""
        try:
            await self.loop.memory_router.commit_turn(
                MemoryCommitRequest(
                    scope=scope,
                    inbound_content=inbound_content,
                    outbound_content=outbound_content,
                    persisted_messages=tuple(persisted_messages),
                )
            )
        except Exception:
            logger.exception("Memory router commit failed for {}", scope.session_key)

    async def flush_memory_session(
        self,
        session: Session,
        *,
        channel: str,
        chat_id: str,
        sender_id: str | None,
        persona: str | None = None,
        language: str | None = None,
    ) -> None:
        """Flush buffered memory state before persona/session transitions."""
        scope = self.memory_scope(
            session,
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            persona=persona,
            language=language,
        )
        try:
            await self.loop.memory_router.flush_session(scope)
        except Exception:
            logger.exception("Memory router flush failed for {}", scope.session_key)

    def load_session_turn_state(
        self,
        *,
        key: str,
        channel: str,
        chat_id: str,
    ) -> _SessionTurnState:
        """Resolve the active session and its persona/language metadata."""
        session = self.loop.sessions.get_or_create(key)
        restored = self.loop._restore_runtime_checkpoint(session)
        restored = self.loop._restore_pending_user_turn(session) or restored
        if restored:
            self.loop.sessions.save(session)
        persona = self.get_session_persona(session)
        language = self.get_session_language(session)
        session, pending = self.loop.auto_compact.prepare_session(session, key)
        return self.loop._session_turn_state_type()(
            key=key,
            session=session,
            channel=channel,
            chat_id=chat_id,
            persona=persona,
            language=language,
            pending_summary=pending,
        )
