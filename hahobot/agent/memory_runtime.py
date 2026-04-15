"""Memory backend selection and router construction helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from hahobot.agent.memory_backends.base import UserMemoryBackend

if TYPE_CHECKING:
    from hahobot.agent.memory_router import MemoryRouter
    from hahobot.config.schema import MemoryConfig


class MemoryRuntimeManager:
    """Build the active memory router from runtime config."""

    def __init__(
        self,
        *,
        config: MemoryConfig,
        file_backend_factory: Callable[[], UserMemoryBackend],
        mem0_backend_type: type[UserMemoryBackend],
        memory_router_factory: Callable[..., MemoryRouter],
    ) -> None:
        self.config = config
        self._file_backend_factory = file_backend_factory
        self._mem0_backend_type = mem0_backend_type
        self._memory_router_factory = memory_router_factory

    def update_runtime(self, config: MemoryConfig | None = None) -> None:
        """Refresh the runtime-bound memory config."""
        if config is not None:
            self.config = config

    def build_user_backend(self, config: MemoryConfig | None = None) -> UserMemoryBackend:
        """Create the configured primary user-memory backend."""
        resolved = config or self.config
        if resolved.user.backend == "mem0":
            return self._mem0_backend_type(resolved.user.mem0)
        return self._file_backend_factory()

    def build_fallback_backend(
        self,
        config: MemoryConfig | None = None,
        primary: UserMemoryBackend | None = None,
    ) -> UserMemoryBackend | None:
        """Keep file-backed memory as the conservative fallback when Mem0 is primary."""
        resolved = config or self.config
        if resolved.user.backend == "mem0":
            return self._file_backend_factory()
        return None

    def build_shadow_backends(
        self,
        config: MemoryConfig | None = None,
        primary: UserMemoryBackend | None = None,
    ) -> list[UserMemoryBackend]:
        """Create optional shadow backends that receive writes in parallel."""
        resolved = config or self.config
        current_primary = primary or self.build_user_backend(resolved)
        if resolved.user.shadow_write_mem0 and not isinstance(current_primary, self._mem0_backend_type):
            return [self._mem0_backend_type(resolved.user.mem0)]
        return []

    def build_router(self, config: MemoryConfig | None = None):
        """Construct the runtime memory router for the active config."""
        resolved = config or self.config
        user_backend = self.build_user_backend(resolved)
        fallback_backend = self.build_fallback_backend(resolved, user_backend)
        shadow_backends = self.build_shadow_backends(resolved, user_backend)
        return self._memory_router_factory(
            user_backend=user_backend,
            fallback_backend=fallback_backend,
            shadow_backends=shadow_backends,
        )
