"""Privacy-aware chat commands for importing local memory into shared Mem0."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from typing import TYPE_CHECKING, Any

from loguru import logger

from hahobot.agent.i18n import text
from hahobot.agent.memory_shared_backfill import (
    build_shared_memory_backfill_plan,
    execute_shared_memory_backfill,
    shared_memory_backfill_plan_fingerprint,
    validate_backfill_config,
)
from hahobot.agent.personas import resolve_persona_name
from hahobot.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.agent.memory_shared_backfill import SharedMemoryBackfillPlan
    from hahobot.session.manager import Session


_CONFIRMATION_KEY = "_shared_memory_backfill_confirmation"
_CONFIRMATION_VERSION = 1
_CONFIRMATION_TTL_SECONDS = 10 * 60


def shared_memory_backfill_available(config: Any) -> bool:
    """Return whether a shared-memory config can safely accept a backfill."""
    try:
        validate_backfill_config(config)
    except (AttributeError, TypeError, ValueError):
        return False
    return True


class MemoryCommandHandler:
    """Handle the guarded two-step ``/memory backfill`` chat workflow."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    @staticmethod
    def _response(msg: InboundMessage, content: str) -> OutboundMessage:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata={"render_as": "text"},
        )

    def available(self) -> bool:
        """Return whether the current hot-reloaded config exposes this command."""
        return shared_memory_backfill_available(self.loop.memory_config.shared)

    async def handle(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        language = self.loop._get_session_language(session)
        if not self.available():
            return self._response(msg, text(language, "memory_backfill_unavailable"))

        parts = msg.content.strip().split()
        if len(parts) < 3 or parts[1].casefold() != "backfill":
            return self._response(msg, text(language, "memory_backfill_usage"))

        action = parts[2].casefold()
        if action == "preview" and len(parts) <= 4:
            persona = parts[3] if len(parts) == 4 else None
            return await self._preview(msg, session, language, persona)
        if action == "confirm" and len(parts) == 4:
            return await self._confirm(msg, session, language, parts[3])
        return self._response(msg, text(language, "memory_backfill_usage"))

    async def _preview(
        self,
        msg: InboundMessage,
        session: Session,
        language: str,
        persona: str | None,
    ) -> OutboundMessage:
        self._clear_confirmation(session)

        selected: list[str] | None = None
        if persona is not None:
            resolved = await asyncio.to_thread(resolve_persona_name, self.loop.workspace, persona)
            if resolved is None:
                available = await asyncio.to_thread(self.loop.context.list_personas)
                return self._response(
                    msg,
                    text(
                        language,
                        "unknown_persona",
                        name=persona,
                        personas=", ".join(available),
                        path=self.loop.workspace / "personas" / persona,
                    ),
                )
            selected = [resolved]

        try:
            plan = await self._build_plan(selected)
        except Exception:
            logger.exception("/memory backfill preview failed for {}", session.key)
            return self._response(msg, text(language, "memory_backfill_preview_failed"))

        totals = plan.to_dict(mode="dry_run")["totals"]
        personas = ", ".join(plan.selected_personas)
        if not plan.items:
            return self._response(
                msg,
                self._append_warning_summary(
                    language,
                    text(
                        language,
                        "memory_backfill_preview_empty",
                        personas=personas,
                        files=totals["filesScanned"],
                        missing=totals["filesMissing"],
                        skipped=totals["skipped"],
                        warnings=len(plan.warnings),
                    ),
                    plan,
                ),
            )

        token = secrets.token_urlsafe(9)
        session.metadata[_CONFIRMATION_KEY] = {
            "version": _CONFIRMATION_VERSION,
            "token_sha256": self._token_digest(token),
            "plan_fingerprint": shared_memory_backfill_plan_fingerprint(
                plan, self.loop.memory_config.shared
            ),
            "expires_at": time.time() + _CONFIRMATION_TTL_SECONDS,
            "personas": list(plan.selected_personas),
            "session_key": session.key,
            "channel": msg.channel,
            "chat_id": msg.chat_id,
            "sender_id": msg.sender_id,
        }
        self.loop.sessions.save(session)
        return self._response(
            msg,
            self._append_warning_summary(
                language,
                text(
                    language,
                    "memory_backfill_preview_ready",
                    personas=personas,
                    writes=totals["candidateWrites"],
                    public=totals["publicWrites"],
                    private=totals["personaPrivateWrites"],
                    skipped=totals["skipped"],
                    warnings=len(plan.warnings),
                    token=token,
                    minutes=_CONFIRMATION_TTL_SECONDS // 60,
                ),
                plan,
            ),
        )

    async def _confirm(
        self,
        msg: InboundMessage,
        session: Session,
        language: str,
        token: str,
    ) -> OutboundMessage:
        record = self._confirmation_record(session)
        if record is None:
            if _CONFIRMATION_KEY in session.metadata:
                self._clear_confirmation(session)
            return self._response(msg, text(language, "memory_backfill_confirmation_invalid"))
        if not self._confirmation_origin_matches(record, msg, session):
            return self._response(msg, text(language, "memory_backfill_confirmation_invalid"))

        if time.time() > record["expires_at"]:
            self._clear_confirmation(session)
            return self._response(msg, text(language, "memory_backfill_confirmation_expired"))

        if not hmac.compare_digest(record["token_sha256"], self._token_digest(token)):
            return self._response(msg, text(language, "memory_backfill_confirmation_invalid"))

        try:
            plan = await self._build_plan(record["personas"])
            fingerprint = shared_memory_backfill_plan_fingerprint(
                plan, self.loop.memory_config.shared
            )
        except Exception:
            logger.exception("/memory backfill confirmation planning failed for {}", session.key)
            self._clear_confirmation(session)
            return self._response(msg, text(language, "memory_backfill_preview_failed"))

        if not hmac.compare_digest(record["plan_fingerprint"], fingerprint):
            self._clear_confirmation(session)
            return self._response(msg, text(language, "memory_backfill_plan_changed"))

        # ``process_direct`` callers are not necessarily behind DispatchRuntime's
        # per-session lock. Re-check the live record after the thread hop, then
        # consume it before any network await so two concurrent confirms cannot
        # execute the same preview.
        if self._confirmation_record(session) != record:
            return self._response(msg, text(language, "memory_backfill_confirmation_invalid"))
        self._clear_confirmation(session)

        try:
            mode, statuses = await execute_shared_memory_backfill(
                plan,
                self.loop.memory_config.shared,
                state_root=self.loop._shared_memory_state_root(),
                force=False,
            )
        except Exception:
            logger.exception("/memory backfill execution failed for {}", session.key)
            return self._response(msg, text(language, "memory_backfill_execute_failed"))
        return self._response(msg, self._render_result(language, plan, mode, statuses))

    async def _build_plan(self, personas: list[str] | None) -> SharedMemoryBackfillPlan:
        return await asyncio.to_thread(
            build_shared_memory_backfill_plan,
            self.loop.workspace,
            self.loop.memory_config.shared,
            personas=personas,
        )

    def _clear_confirmation(self, session: Session) -> None:
        if session.metadata.pop(_CONFIRMATION_KEY, None) is not None:
            self.loop.sessions.save(session)

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _confirmation_record(session: Session) -> dict[str, Any] | None:
        raw = session.metadata.get(_CONFIRMATION_KEY)
        if not isinstance(raw, dict) or raw.get("version") != _CONFIRMATION_VERSION:
            return None
        required_strings = (
            "token_sha256",
            "plan_fingerprint",
            "session_key",
            "channel",
            "chat_id",
            "sender_id",
        )
        if any(not isinstance(raw.get(key), str) or not raw[key] for key in required_strings):
            return None
        expires_at = raw.get("expires_at")
        personas = raw.get("personas")
        if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
            return None
        if not isinstance(personas, list) or any(
            not isinstance(persona, str) or not persona for persona in personas
        ):
            return None
        return raw

    @staticmethod
    def _confirmation_origin_matches(
        record: dict[str, Any],
        msg: InboundMessage,
        session: Session,
    ) -> bool:
        return (
            record["session_key"] == session.key
            and record["channel"] == msg.channel
            and record["chat_id"] == msg.chat_id
            and record["sender_id"] == msg.sender_id
        )

    @staticmethod
    def _append_warning_summary(
        language: str,
        content: str,
        plan: SharedMemoryBackfillPlan,
    ) -> str:
        """Append localized, content-free warning categories to a preview."""
        historical = sum(warning.startswith("Older local persistence") for warning in plan.warnings)
        unclosed = sum(warning.startswith("Unclosed <") for warning in plan.warnings)
        other = len(plan.warnings) - historical - unclosed
        if not plan.warnings:
            return content

        lines = [text(language, "memory_backfill_warning_header")]
        if historical:
            lines.append(text(language, "memory_backfill_warning_historical_privacy"))
        if unclosed:
            lines.append(text(language, "memory_backfill_warning_unclosed", count=unclosed))
        if other:
            lines.append(text(language, "memory_backfill_warning_other", count=other))
        return f"{content}\n\n" + "\n".join(lines)

    @staticmethod
    def _render_result(
        language: str,
        plan: SharedMemoryBackfillPlan,
        mode: str,
        statuses: dict[str, str],
    ) -> str:
        counts: dict[str, int] = {}
        for status in statuses.values():
            counts[status] = counts.get(status, 0) + 1
        kwargs = {
            "writes": len(plan.items),
            "delivered": counts.get("delivered", 0),
            "already_imported": counts.get("already_imported", 0),
            "pending": sum(
                counts.get(status, 0) for status in ("queued", "pending", "already_queued")
            ),
        }
        key = {
            "complete": "memory_backfill_complete",
            "queued": "memory_backfill_queued",
            "partial": "memory_backfill_partial",
            "no_op": "memory_backfill_preview_empty",
        }.get(mode, "memory_backfill_partial")
        if key == "memory_backfill_preview_empty":
            return text(
                language,
                key,
                personas=", ".join(plan.selected_personas),
                files=plan.files_scanned,
                missing=plan.files_missing,
                skipped=len(plan.skipped),
                warnings=len(plan.warnings),
            )
        return text(language, key, **kwargs)
