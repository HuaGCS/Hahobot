"""Abstract interfaces for user memory backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from hahobot.agent.memory_models import MemoryCommitRequest, MemoryScope, ResolvedMemoryContext


class UserMemoryBackend(ABC):
    """Backend abstraction for user-scoped long-term memory."""

    async def start(self) -> None:
        """Start optional background recovery work once an event loop is running."""
        return

    def retire(self) -> None:
        """Stop initiating background work after a runtime generation is replaced."""
        return

    def retire_after_pending_work(self) -> None:
        """Retire after any generation-owned final delivery has completed."""
        self.retire()

    async def close(self) -> None:
        """Release optional background resources during shutdown."""
        self.retire()

    @abstractmethod
    async def resolve_context(self, scope: MemoryScope) -> ResolvedMemoryContext:
        """Return the memory block that should be injected into the prompt."""

    async def commit_turn(self, request: MemoryCommitRequest) -> None:
        """Persist a completed turn to the backend."""
        return

    async def flush_session(self, scope: MemoryScope) -> None:
        """Flush any buffered memory writes for the given scope."""
        return
