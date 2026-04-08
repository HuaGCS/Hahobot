"""File-backed user memory backend."""

from __future__ import annotations

from hahobot.agent.memory import MemoryStore
from hahobot.agent.memory_backends.base import UserMemoryBackend
from hahobot.agent.memory_models import MemoryScope, ResolvedMemoryContext
from hahobot.agent.personas import persona_workspace


class FileUserMemoryBackend(UserMemoryBackend):
    """Read user memory from the existing persona-scoped markdown files."""

    async def resolve_context(self, scope: MemoryScope) -> ResolvedMemoryContext:
        store = MemoryStore(persona_workspace(scope.workspace, scope.persona))
        return ResolvedMemoryContext(block=store.get_memory_context(), source="file")
