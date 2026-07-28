"""Routing layer for user memory reads and writes."""

from __future__ import annotations

from loguru import logger

from hahobot.agent.memory_backends.base import UserMemoryBackend
from hahobot.agent.memory_models import MemoryCommitRequest, MemoryScope, ResolvedMemoryContext


class MemoryRouter:
    """Coordinate memory access through the configured user-memory backend."""

    def __init__(
        self,
        user_backend: UserMemoryBackend,
        fallback_backend: UserMemoryBackend | None = None,
        augment_backends: list[UserMemoryBackend] | None = None,
        shadow_backends: list[UserMemoryBackend] | None = None,
    ):
        self.user_backend = user_backend
        self.fallback_backend = fallback_backend
        self.augment_backends = list(augment_backends or [])
        self.shadow_backends = list(shadow_backends or [])
        self._active_turns = 0
        self._retirement_requested = False
        self._retired = False

    def _backends(self) -> list[UserMemoryBackend]:
        backends: list[UserMemoryBackend] = []
        for backend in [
            self.user_backend,
            self.fallback_backend,
            *self.augment_backends,
            *self.shadow_backends,
        ]:
            if backend is not None and backend not in backends:
                backends.append(backend)
        return backends

    async def start(self) -> None:
        """Start background recovery for every backend in this router generation."""
        if self._retired:
            return
        for backend in self._backends():
            try:
                await backend.start()
            except Exception:
                logger.exception("Memory backend startup failed: {}", type(backend).__name__)

    def retire(self) -> None:
        """Synchronously stop timers/tasks when this router generation is replaced."""
        if self._retired:
            return
        self._retirement_requested = True
        self._retired = True
        for backend in self._backends():
            try:
                backend.retire()
            except Exception:
                logger.exception("Memory backend retirement failed: {}", type(backend).__name__)

    def acquire_turn(self) -> None:
        """Lease this generation across context preparation and its matching commit."""
        if self._retirement_requested or self._retired:
            raise RuntimeError("Cannot acquire a retired memory router generation")
        self._active_turns += 1

    def release_turn(self) -> None:
        """Release a turn lease and finish deferred retirement when it becomes idle."""
        if self._active_turns <= 0:
            return
        self._active_turns -= 1
        if self._active_turns == 0 and self._retirement_requested:
            self._retire_after_pending_work()

    def request_retirement(self) -> None:
        """Defer retirement until turns that prepared with this generation commit."""
        if self._retirement_requested:
            return
        self._retirement_requested = True
        if self._active_turns == 0:
            self._retire_after_pending_work()

    def _retire_after_pending_work(self) -> None:
        if self._retired:
            return
        self._retired = True
        for backend in self._backends():
            try:
                backend.retire_after_pending_work()
            except Exception:
                logger.exception(
                    "Memory backend deferred retirement failed: {}",
                    type(backend).__name__,
                )

    async def close(self) -> None:
        """Close every backend after preventing any new background work."""
        self.retire()
        for backend in self._backends():
            try:
                await backend.close()
            except Exception:
                logger.exception("Memory backend close failed: {}", type(backend).__name__)

    async def prepare_context(self, scope: MemoryScope) -> ResolvedMemoryContext:
        """Resolve the memory block that should enter the current prompt."""
        resolved = await self._prepare_local_context(scope)
        external_blocks: list[str] = []
        external_sources: list[str] = []
        for backend in self.augment_backends:
            try:
                augmented = await backend.resolve_context(scope)
            except Exception:
                logger.exception(
                    "Memory augment context resolution failed: {}", type(backend).__name__
                )
                continue
            if augmented.block.strip():
                external_blocks.append(augmented.block.strip())
                external_sources.append(augmented.source)

        if not external_blocks:
            return resolved
        sources = "+".join([resolved.source, *external_sources])
        return ResolvedMemoryContext(
            block=resolved.block,
            source=sources,
            external_block="\n\n".join(external_blocks),
        )

    async def _prepare_local_context(self, scope: MemoryScope) -> ResolvedMemoryContext:
        """Resolve the always-on local memory context with its conservative fallback."""
        try:
            resolved = await self.user_backend.resolve_context(scope)
        except Exception:
            logger.exception(
                "Memory backend context resolution failed: {}", type(self.user_backend).__name__
            )
            if self.fallback_backend is None:
                raise
            logger.warning(
                "Falling back to {} for memory context",
                type(self.fallback_backend).__name__,
            )
            return await self.fallback_backend.resolve_context(scope)

        if resolved.block.strip() or self.fallback_backend is None:
            return resolved

        logger.debug(
            "Primary memory backend {} returned empty context; falling back to {}",
            type(self.user_backend).__name__,
            type(self.fallback_backend).__name__,
        )
        return await self.fallback_backend.resolve_context(scope)

    async def commit_turn(self, request: MemoryCommitRequest) -> None:
        """Persist a completed turn through the active backend."""
        backends = [self.user_backend, *self.shadow_backends]
        for backend in backends:
            try:
                await backend.commit_turn(request)
            except Exception:
                logger.exception("Memory backend commit failed: {}", type(backend).__name__)

    async def flush_session(self, scope: MemoryScope) -> None:
        """Flush backend state for a session before scope-sensitive transitions."""
        for backend in self._backends():
            try:
                await backend.flush_session(scope)
            except Exception:
                logger.exception("Memory backend flush failed: {}", type(backend).__name__)
