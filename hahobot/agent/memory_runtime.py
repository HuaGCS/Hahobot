"""Memory backend selection and router construction helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

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
        sqlite_backend_factory: Callable[[], UserMemoryBackend],
        memory_router_factory: Callable[..., MemoryRouter],
    ) -> None:
        self.config = config
        self._file_backend_factory = file_backend_factory
        self._sqlite_backend_factory = sqlite_backend_factory
        self._memory_router_factory = memory_router_factory

    def update_runtime(self, config: MemoryConfig | None = None) -> None:
        """Refresh the runtime-bound memory config."""
        if config is not None:
            self.config = config

    def build_user_backend(self, config: MemoryConfig | None = None) -> UserMemoryBackend:
        """Create the configured primary user-memory backend."""
        resolved = config or self.config
        if resolved.user.backend == "sqlite":
            return self._sqlite_backend_factory()
        return self._file_backend_factory()

    def build_fallback_backend(
        self,
        config: MemoryConfig | None = None,
        primary: UserMemoryBackend | None = None,
    ) -> UserMemoryBackend | None:
        """Use the file backend as a conservative fallback when SQLite is primary."""
        resolved = config or self.config
        if resolved.user.backend == "sqlite":
            return self._file_backend_factory()
        return None

    def build_shadow_backends(
        self,
        config: MemoryConfig | None = None,
        primary: UserMemoryBackend | None = None,
    ) -> list[UserMemoryBackend]:
        """No shadow backends configured."""
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
