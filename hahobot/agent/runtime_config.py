"""Runtime configuration and hot-reload helpers for AgentLoop."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from hahobot.agent.memory import Dream
from hahobot.agent.memory_backends.base import UserMemoryBackend
from hahobot.agent.tools.policy import RuntimeToolPolicy

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.config.schema import MemoryConfig


class RuntimeConfigManager:
    """Own hot-reloadable runtime config application for one AgentLoop."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    def sync_tool_runtime_state(self) -> None:
        """Keep the delegated tool runtime aligned with mutable loop settings."""
        self.loop._tool_runtime.update_runtime(
            workspace=self.loop.workspace,
            exec_config=self.loop.exec_config,
            image_gen_config=self.loop.image_gen_config,
            restrict_to_workspace=self.loop.restrict_to_workspace,
            web_enabled=self.loop.web_enabled,
            brave_api_key=self.loop.brave_api_key,
            web_proxy=self.loop.web_proxy,
            web_search_provider=self.loop.web_search_provider,
            web_search_base_url=self.loop.web_search_base_url,
            web_search_max_results=self.loop.web_search_max_results,
            history_index_backend=self.loop.memory_config.archive.index_backend,
            timezone=self.loop.context.timezone,
        )

    def tool_policy(self) -> RuntimeToolPolicy:
        """Build the current internal tool policy view."""
        self.sync_tool_runtime_state()
        return self.loop._tool_runtime.policy()

    def apply_runtime_tool_config(self) -> None:
        """Apply runtime-configurable settings to already-registered tools."""
        self.sync_tool_runtime_state()
        self.loop._tool_runtime.apply_runtime_config()

    def rebind_runtime_workspace(self, workspace: Path) -> None:
        """Switch runtime-bound workspace references in place."""
        self.loop.workspace = workspace
        self.loop._mcp_runtime.update_runtime(workspace=workspace)
        self.loop.voice_replies.update_runtime(
            workspace=workspace,
            channels_config=self.loop.channels_config,
        )
        self.loop.context.rebind_runtime(
            workspace=workspace,
            timezone=self.loop.context.timezone,
            disabled_skills=self.loop._disabled_skills,
        )
        self.sync_tool_runtime_state()
        self.loop.sessions.rebind_workspace(workspace)
        self.loop.memory_consolidator.rebind_runtime(
            workspace=workspace,
            sessions=self.loop.sessions,
        )
        self.loop.memory_consolidator.store = self.loop.context.memory
        self.loop.consolidator = self.loop.memory_consolidator
        self.loop.dream = Dream(
            store=self.loop.context.memory,
            provider=self.loop.provider,
            model=self.loop.model,
            max_batch_size=self.loop.dream.max_batch_size,
            max_iterations=self.loop.dream.max_iterations,
            max_tool_result_chars=self.loop.dream.max_tool_result_chars,
        )

    def configure_memory_router(self) -> None:
        """Build the current memory router from runtime config."""
        self.loop._memory_runtime.update_runtime(self.loop.memory_config)
        self.loop.memory_router = self.loop._memory_runtime.build_router()

    def build_user_memory_backend(self, config: MemoryConfig) -> UserMemoryBackend:
        """Create the configured primary user-memory backend."""
        return self.loop._memory_runtime.build_user_backend(config)

    def build_memory_fallback_backend(
        self,
        config: MemoryConfig,
        primary: UserMemoryBackend,
    ) -> UserMemoryBackend | None:
        """Keep file-backed memory as the conservative fallback when Mem0 is primary."""
        return self.loop._memory_runtime.build_fallback_backend(config, primary)

    def build_shadow_memory_backends(
        self,
        config: MemoryConfig,
        primary: UserMemoryBackend,
    ) -> list[UserMemoryBackend]:
        """Create optional shadow backends that receive writes in parallel."""
        return self.loop._memory_runtime.build_shadow_backends(config, primary)

    def apply_runtime_config(self, config) -> bool:
        """Apply hot-reloadable config to the current agent instance."""
        from hahobot.providers.base import GenerationSettings

        defaults = config.agents.defaults
        tools_cfg = config.tools
        web_cfg = tools_cfg.web
        search_cfg = web_cfg.search
        next_workspace = config.workspace_path
        next_timezone = defaults.timezone

        if next_workspace.resolve(strict=False) != self.loop.workspace.resolve(strict=False):
            self.rebind_runtime_workspace(next_workspace)

        self.loop._disabled_skills = list(defaults.disabled_skills)
        self.loop.context.rebind_runtime(
            workspace=self.loop.workspace,
            timezone=next_timezone,
            disabled_skills=self.loop._disabled_skills,
        )

        self.loop.model = defaults.model
        self.loop.max_iterations = defaults.max_tool_iterations
        self.loop.context_window_tokens = defaults.context_window_tokens
        self.loop.context_block_limit = defaults.context_block_limit
        self.loop.max_tool_result_chars = defaults.max_tool_result_chars
        self.loop.provider_retry_mode = defaults.provider_retry_mode
        self.loop.auto_compact.set_session_ttl_minutes(defaults.session_ttl_minutes)
        self.loop.exec_config = tools_cfg.exec
        self.loop.image_gen_config = tools_cfg.image_gen
        self.loop.memory_config = config.memory
        self.loop.restrict_to_workspace = tools_cfg.restrict_to_workspace
        self.loop.web_enabled = web_cfg.enable
        self.loop.brave_api_key = search_cfg.api_key or None
        self.loop.web_proxy = web_cfg.proxy or None
        self.loop.web_search_provider = search_cfg.provider
        self.loop.web_search_base_url = search_cfg.base_url or None
        self.loop.web_search_max_results = search_cfg.max_results
        self.loop.channels_config = config.channels
        self.loop.voice_replies.update_runtime(
            workspace=self.loop.workspace,
            channels_config=self.loop.channels_config,
        )

        self.loop.provider.generation = GenerationSettings(
            temperature=defaults.temperature,
            max_tokens=defaults.max_tokens,
            reasoning_effort=defaults.reasoning_effort,
        )
        if hasattr(self.loop.provider, "default_model"):
            self.loop.provider.default_model = self.loop.model
        self.loop.memory_consolidator.model = self.loop.model
        self.loop.memory_consolidator.context_window_tokens = self.loop.context_window_tokens
        self.loop.memory_consolidator.max_completion_tokens = defaults.max_tokens
        self.loop.subagents.apply_runtime_config(
            workspace=self.loop.workspace,
            model=self.loop.model,
            brave_api_key=self.loop.brave_api_key,
            web_proxy=self.loop.web_proxy,
            web_enabled=self.loop.web_enabled,
            web_search_provider=self.loop.web_search_provider,
            web_search_base_url=self.loop.web_search_base_url,
            web_search_max_results=self.loop.web_search_max_results,
            exec_config=self.loop.exec_config,
            restrict_to_workspace=self.loop.restrict_to_workspace,
            disabled_skills=self.loop._disabled_skills,
        )
        self.configure_memory_router()
        self.apply_runtime_tool_config()

        mcp_changed = self.loop._dump_mcp_servers(config.tools.mcp_servers) != self.loop._dump_mcp_servers(
            self.loop._mcp_servers
        )
        self.loop._mcp_runtime.update_runtime(
            workspace=self.loop.workspace,
            servers=config.tools.mcp_servers,
        )
        return mcp_changed

    async def reload_runtime_config(self, config=None, *, force: bool = False) -> None:
        """Public wrapper for applying hot-reloadable runtime config."""
        if config is not None:
            if self.loop.config_path and self.loop.config_path.exists():
                self.loop._runtime_config_mtime_ns = self.loop.config_path.stat().st_mtime_ns
            if self.apply_runtime_config(config):
                await self.loop._reset_mcp_connections()
            return
        await self.reload_runtime_config_if_needed(force=force)

    async def reload_runtime_config_if_needed(self, *, force: bool = False) -> None:
        """Reload hot-reloadable config from the active config file when it changes."""
        if self.loop.config_path is None:
            return

        try:
            mtime_ns = self.loop.config_path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime_ns = None

        if not force and mtime_ns == self.loop._runtime_config_mtime_ns:
            return

        self.loop._runtime_config_mtime_ns = mtime_ns

        from hahobot.config.loader import load_config

        if mtime_ns is None:
            await self.loop._reset_mcp_connections()
            self.loop._mcp_servers = {}
            return

        reloaded = load_config(self.loop.config_path)
        if self.apply_runtime_config(reloaded):
            await self.loop._reset_mcp_connections()

    async def reload_mcp_servers_if_needed(self, *, force: bool = False) -> None:
        """Backward-compatible wrapper for runtime config reloads."""
        await self.reload_runtime_config_if_needed(force=force)

    def register_default_tools(self) -> None:
        """Register the default set of tools."""
        self.sync_tool_runtime_state()
        self.loop._tool_runtime.register_default_tools()

    async def connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        await self.reload_mcp_servers_if_needed()
        await self.loop._mcp_runtime.connect()
