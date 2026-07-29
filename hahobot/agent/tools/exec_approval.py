"""In-memory, session-scoped approval queue for guarded shell execution."""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ExecApprovalContext:
    """Origin identity attached to an exec request without shared mutable state."""

    session_key: str
    sender_id: str
    channel: str
    chat_id: str
    generation: int = 0

    @property
    def valid(self) -> bool:
        return bool(self.session_key and self.sender_id and self.channel and self.chat_id)


@dataclass(frozen=True, slots=True)
class PendingExecRequest:
    """One already-admission-checked command awaiting explicit user approval."""

    request_id: str
    context: ExecApprovalContext
    command: str = field(repr=False)
    working_dir: str
    timeout: int | None
    created_at: float
    expires_at: float
    sequence: int

    @property
    def command_preview(self) -> str:
        """Return the full command with whitespace/control characters visible."""
        return json.dumps(self.command, ensure_ascii=True)

    @property
    def working_dir_preview(self) -> str:
        """Return the full working directory in the same unambiguous form."""
        return json.dumps(self.working_dir, ensure_ascii=True)

    @property
    def approval_preview(self) -> str:
        """Render everything the user must verify before one-shot execution."""
        return f"command={self.command_preview}\nworking_dir={self.working_dir_preview}"


class ExecApprovalStore:
    """Process-local pending approvals shared by main and subagent ExecTools."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 10 * 60,
        max_pending_per_scope: int = 32,
        max_pending_total: int = 256,
        max_command_chars: int = 4_096,
        max_command_preview_chars: int = 8_000,
        max_result_chars: int = 16_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = max(0.001, float(ttl_seconds))
        self.max_pending_per_scope = max(1, int(max_pending_per_scope))
        self.max_pending_total = max(1, int(max_pending_total))
        self.max_command_chars = max(1, int(max_command_chars))
        self.max_command_preview_chars = max(1, int(max_command_preview_chars))
        self.max_result_chars = max(1, int(max_result_chars))
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: dict[str, PendingExecRequest] = {}
        self._session_generations: dict[str, int] = {}
        self._sequence = 0
        self._context: ContextVar[ExecApprovalContext | None] = ContextVar(
            f"exec_approval_context_{id(self)}",
            default=None,
        )

    def set_context(
        self,
        *,
        session_key: str,
        sender_id: str,
        channel: str,
        chat_id: str,
    ) -> None:
        """Bind an origin to the current async context only."""
        self.bind_context(
            self.make_context(
                session_key=session_key,
                sender_id=sender_id,
                channel=channel,
                chat_id=chat_id,
            )
        )

    def make_context(
        self,
        *,
        session_key: str,
        sender_id: str,
        channel: str,
        chat_id: str,
    ) -> ExecApprovalContext:
        """Capture the current generation for one turn/subagent origin."""
        normalized_session = str(session_key or "")
        with self._lock:
            generation = self._session_generations.get(normalized_session, 0)
        return ExecApprovalContext(
            session_key=normalized_session,
            sender_id=str(sender_id or ""),
            channel=str(channel or ""),
            chat_id=str(chat_id or ""),
            generation=generation,
        )

    def bind_context(self, context: ExecApprovalContext) -> None:
        """Bind an already captured turn origin to this async task."""
        self._context.set(context)

    def current_context(self) -> ExecApprovalContext | None:
        """Return the task-local origin, if one was bound."""
        context = self._context.get()
        return context if context is not None and context.valid else None

    def context_is_current(self, context: ExecApprovalContext) -> bool:
        """Return False after a reset invalidated the context's generation."""
        with self._lock:
            return context.generation == self._session_generations.get(context.session_key, 0)

    def enqueue(
        self,
        *,
        command: str,
        working_dir: str,
        timeout: int | None,
        context: ExecApprovalContext | None = None,
    ) -> PendingExecRequest | None:
        """Queue a request for the current origin; fail closed without context."""
        origin = context or self.current_context()
        if origin is None or not origin.valid or not self.command_is_presentable(command):
            return None
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            if origin.generation != self._session_generations.get(origin.session_key, 0):
                return None
            duplicate = next(
                (
                    request
                    for request in self._pending.values()
                    if request.context == origin
                    and request.command == command
                    and request.working_dir == working_dir
                    and request.timeout == timeout
                ),
                None,
            )
            if duplicate is not None:
                return duplicate
            if len(self._pending) >= self.max_pending_total:
                return None
            scope_size = sum(
                request.context.session_key == origin.session_key
                and request.context.sender_id == origin.sender_id
                for request in self._pending.values()
            )
            if scope_size >= self.max_pending_per_scope:
                return None
            self._sequence += 1
            request_id = secrets.token_hex(4)
            while request_id in self._pending:
                request_id = secrets.token_hex(4)
            request = PendingExecRequest(
                request_id=request_id,
                context=origin,
                command=command,
                working_dir=working_dir,
                timeout=timeout,
                created_at=now,
                expires_at=now + self.ttl_seconds,
                sequence=self._sequence,
            )
            self._pending[request.request_id] = request
            return request

    def consume(
        self,
        *,
        session_key: str,
        sender_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
        all_pending: bool = False,
    ) -> list[PendingExecRequest]:
        """Atomically consume the oldest or all currently matching requests."""
        with self._lock:
            self._prune_locked(self._clock())
            matches = sorted(
                (
                    request
                    for request in self._pending.values()
                    if request.context.session_key == session_key
                    and request.context.sender_id == sender_id
                    and (channel is None or request.context.channel == channel)
                    and (chat_id is None or request.context.chat_id == chat_id)
                ),
                key=lambda request: request.sequence,
            )
            selected = matches if all_pending else matches[:1]
            for request in selected:
                self._pending.pop(request.request_id, None)
            return selected

    def pending_for(
        self,
        *,
        session_key: str,
        sender_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[PendingExecRequest]:
        """Inspect matching live requests without consuming them."""
        with self._lock:
            self._prune_locked(self._clock())
            return sorted(
                (
                    request
                    for request in self._pending.values()
                    if request.context.session_key == session_key
                    and request.context.sender_id == sender_id
                    and (channel is None or request.context.channel == channel)
                    and (chat_id is None or request.context.chat_id == chat_id)
                ),
                key=lambda request: request.sequence,
            )

    def clear_session(self, session_key: str) -> int:
        """Invalidate every pending request for one logical session."""
        with self._lock:
            self._session_generations[session_key] = (
                self._session_generations.get(session_key, 0) + 1
            )
            targets = [
                request_id
                for request_id, request in self._pending.items()
                if request.context.session_key == session_key
            ]
            for request_id in targets:
                self._pending.pop(request_id, None)
            return len(targets)

    def discard(self, request_id: str) -> bool:
        """Remove one newly queued request that cannot be shown without truncation."""
        with self._lock:
            return self._pending.pop(request_id, None) is not None

    def set_result_budget(self, max_chars: int) -> None:
        """Hot-reload the maximum untruncated model-facing confirmation result."""
        with self._lock:
            self.max_result_chars = max(1, int(max_chars))

    def command_is_presentable(self, command: str) -> bool:
        """Return whether the exact escaped command fits the approval surface."""
        return (
            len(command) <= self.max_command_chars
            and len(json.dumps(command, ensure_ascii=True)) <= self.max_command_preview_chars
        )

    def _prune_locked(self, now: float) -> None:
        expired = [
            request_id for request_id, request in self._pending.items() if request.expires_at <= now
        ]
        for request_id in expired:
            self._pending.pop(request_id, None)
