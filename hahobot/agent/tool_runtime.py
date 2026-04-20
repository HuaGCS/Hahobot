"""Tool registration and hot-reload helpers for AgentLoop."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from hahobot.agent.tools.cron import CronTool
from hahobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from hahobot.agent.tools.history import HistoryExpandTool, HistorySearchTool
from hahobot.agent.tools.image_gen import ImageGenTool
from hahobot.agent.tools.message import MessageTool
from hahobot.agent.tools.notebook import NotebookEditTool
from hahobot.agent.tools.policy import RuntimeToolPolicy
from hahobot.agent.tools.search import GlobTool, GrepTool
from hahobot.agent.tools.self_inspect import SelfInspectTool
from hahobot.agent.tools.shell import ExecTool
from hahobot.agent.tools.spawn import SpawnTool
from hahobot.agent.tools.web import WebFetchTool, WebSearchTool

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.agent.tools.registry import ToolRegistry
    from hahobot.bus.events import OutboundMessage
    from hahobot.config.schema import ExecToolConfig, ImageGenConfig
    from hahobot.cron.service import CronService


_UNSET = object()


class ToolRuntimeManager:
    """Own built-in tool registration, workspace scope, and runtime updates."""

    def __init__(
        self,
        *,
        loop: AgentLoop,
        tools: ToolRegistry,
        workspace: Path,
        send_callback: Callable[[OutboundMessage], Awaitable[None]],
        subagents: Any,
        exec_config: ExecToolConfig,
        image_gen_config: ImageGenConfig,
        restrict_to_workspace: bool = False,
        web_enabled: bool = True,
        brave_api_key: str | None = None,
        web_proxy: str | None = None,
        web_search_provider: str = "brave",
        web_search_base_url: str | None = None,
        web_search_max_results: int = 5,
        timezone: str | None = None,
        cron_service: CronService | None = None,
        builtin_read_dirs: tuple[Path, ...] = (),
    ) -> None:
        self.loop = loop
        self.tools = tools
        self.workspace = workspace
        self.send_callback = send_callback
        self.subagents = subagents
        self.exec_config = exec_config
        self.image_gen_config = image_gen_config
        self.restrict_to_workspace = restrict_to_workspace
        self.web_enabled = web_enabled
        self.brave_api_key = brave_api_key
        self.web_proxy = web_proxy
        self.web_search_provider = web_search_provider
        self.web_search_base_url = web_search_base_url
        self.web_search_max_results = web_search_max_results
        self.timezone = timezone
        self.cron_service = cron_service
        self.builtin_read_dirs = builtin_read_dirs

    def update_runtime(
        self,
        *,
        workspace: Path | object = _UNSET,
        exec_config: ExecToolConfig | object = _UNSET,
        image_gen_config: ImageGenConfig | object = _UNSET,
        restrict_to_workspace: bool | object = _UNSET,
        web_enabled: bool | object = _UNSET,
        brave_api_key: str | None | object = _UNSET,
        web_proxy: str | None | object = _UNSET,
        web_search_provider: str | object = _UNSET,
        web_search_base_url: str | None | object = _UNSET,
        web_search_max_results: int | object = _UNSET,
        timezone: str | None | object = _UNSET,
    ) -> None:
        """Refresh runtime-bound tool settings in place."""
        if workspace is not _UNSET:
            self.workspace = workspace
        if exec_config is not _UNSET:
            self.exec_config = exec_config
        if image_gen_config is not _UNSET:
            self.image_gen_config = image_gen_config
        if restrict_to_workspace is not _UNSET:
            self.restrict_to_workspace = restrict_to_workspace
        if web_enabled is not _UNSET:
            self.web_enabled = web_enabled
        if brave_api_key is not _UNSET:
            self.brave_api_key = brave_api_key
        if web_proxy is not _UNSET:
            self.web_proxy = web_proxy
        if web_search_provider is not _UNSET:
            self.web_search_provider = web_search_provider
        if web_search_base_url is not _UNSET:
            self.web_search_base_url = web_search_base_url
        if web_search_max_results is not _UNSET:
            self.web_search_max_results = web_search_max_results
        if timezone is not _UNSET:
            self.timezone = timezone

    def policy(self) -> RuntimeToolPolicy:
        """Build the current internal tool policy view."""
        from hahobot.config.schema import WebSearchConfig, WebToolsConfig

        web_cfg = WebToolsConfig(
            enable=self.web_enabled,
            proxy=self.web_proxy,
            search=WebSearchConfig(
                provider=self.web_search_provider,
                api_key=self.brave_api_key or "",
                base_url=self.web_search_base_url or "",
                max_results=self.web_search_max_results,
            ),
        )
        return RuntimeToolPolicy(
            workspace=self.workspace,
            restrict_to_workspace=self.restrict_to_workspace,
            web_config=web_cfg,
            exec_config=self.exec_config,
            image_gen_config=self.image_gen_config,
            builtin_read_dirs=self.builtin_read_dirs,
        )

    def _sync_optional_tool(
        self,
        *,
        name: str,
        enabled: bool,
        expected_type: type,
        create_tool: Callable[[], Any],
        update_tool: Callable[[Any], None],
    ) -> Any | None:
        """Register, update, or remove one optional runtime-managed tool."""
        existing = self.tools.get(name)
        if not enabled:
            if existing:
                self.tools.unregister(name)
            return None

        if isinstance(existing, expected_type):
            update_tool(existing)
            return existing

        tool = create_tool()
        self.tools.register(tool)
        return tool

    def _exec_tool_kwargs(self) -> dict[str, Any]:
        """Build the current constructor kwargs for the exec tool."""
        return {
            "working_dir": str(self.workspace),
            "timeout": self.exec_config.timeout,
            "restrict_to_workspace": self.restrict_to_workspace,
            "sandbox": self.exec_config.sandbox,
            "path_append": self.exec_config.path_append,
            "allowed_env_keys": list(self.exec_config.allowed_env_keys),
        }

    def _create_exec_tool(self) -> ExecTool:
        """Create an exec tool from current runtime config."""
        return ExecTool(**self._exec_tool_kwargs())

    def _update_exec_tool(self, tool: ExecTool) -> None:
        """Apply current runtime config to an existing exec tool."""
        for attr, value in self._exec_tool_kwargs().items():
            setattr(tool, attr, value)

    def _web_search_tool_kwargs(self) -> dict[str, Any]:
        """Build the current constructor kwargs for the web search tool."""
        return {
            "provider": self.web_search_provider,
            "api_key": self.brave_api_key,
            "base_url": self.web_search_base_url,
            "max_results": self.web_search_max_results,
            "proxy": self.web_proxy,
        }

    def _create_web_search_tool(self) -> WebSearchTool:
        """Create a web search tool from current runtime config."""
        return WebSearchTool(**self._web_search_tool_kwargs())

    def _update_web_search_tool(self, tool: WebSearchTool) -> None:
        """Apply current runtime config to an existing web search tool."""
        config = self._web_search_tool_kwargs()
        tool._init_provider = config["provider"]
        tool._init_api_key = config["api_key"]
        tool._init_base_url = config["base_url"]
        tool.max_results = config["max_results"]
        tool.proxy = config["proxy"]

    def _web_fetch_tool_kwargs(self) -> dict[str, Any]:
        """Build the current constructor kwargs for the web fetch tool."""
        return {"proxy": self.web_proxy}

    def _create_web_fetch_tool(self) -> WebFetchTool:
        """Create a web fetch tool from current runtime config."""
        return WebFetchTool(**self._web_fetch_tool_kwargs())

    def _update_web_fetch_tool(self, tool: WebFetchTool) -> None:
        """Apply current runtime config to an existing web fetch tool."""
        tool.proxy = self._web_fetch_tool_kwargs()["proxy"]

    def _image_gen_tool_kwargs(self) -> dict[str, Any]:
        """Build the current constructor kwargs for the image generation tool."""
        config = self.image_gen_config
        return {
            "workspace": self.workspace,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "model": config.model,
            "proxy": config.proxy or self.web_proxy,
            "timeout": config.timeout,
            "reference_image": config.reference_image,
            "restrict_to_workspace": self.restrict_to_workspace,
        }

    def _sync_image_gen_tool(self) -> None:
        """Register, update, or remove the optional image generation tool."""
        config = self.image_gen_config
        existing = self.tools.get("image_gen")
        if not config.enabled:
            if existing:
                self.tools.unregister("image_gen")
            return

        kwargs = self._image_gen_tool_kwargs()
        if isinstance(existing, ImageGenTool):
            existing.update_config(**kwargs)
            return

        self.tools.register(ImageGenTool(**kwargs))

    def apply_runtime_config(self) -> None:
        """Apply runtime-configurable settings to already-registered tools."""
        policy = self.policy()
        scope = policy.workspace_scope()
        allowed_dir = scope.allowed_dir
        extra_read = list(scope.extra_read_dirs) if scope.extra_read_dirs else None

        if read_tool := self.tools.get("read_file"):
            read_tool._workspace = self.workspace
            read_tool._allowed_dir = allowed_dir
            read_tool._extra_allowed_dirs = extra_read

        for name in ("write_file", "edit_file", "list_dir", "glob", "grep", "notebook_edit"):
            if tool := self.tools.get(name):
                tool._workspace = self.workspace
                tool._allowed_dir = allowed_dir
                tool._extra_allowed_dirs = None

        self._sync_optional_tool(
            name="exec",
            enabled=policy.exec().enabled,
            expected_type=ExecTool,
            create_tool=self._create_exec_tool,
            update_tool=self._update_exec_tool,
        )
        self._sync_optional_tool(
            name="web_search",
            enabled=policy.web().enabled,
            expected_type=WebSearchTool,
            create_tool=self._create_web_search_tool,
            update_tool=self._update_web_search_tool,
        )
        self._sync_optional_tool(
            name="web_fetch",
            enabled=policy.web().enabled,
            expected_type=WebFetchTool,
            create_tool=self._create_web_fetch_tool,
            update_tool=self._update_web_fetch_tool,
        )

        for name in ("history_search", "history_expand"):
            if tool := self.tools.get(name):
                if hasattr(tool, "update_workspace"):
                    tool.update_workspace(self.workspace)

        if cron_tool := self.tools.get("cron"):
            if hasattr(cron_tool, "set_default_timezone"):
                cron_tool.set_default_timezone(self.timezone or "UTC")

        self._sync_image_gen_tool()

    def register_default_tools(self) -> None:
        """Register the default built-in tool set for one agent runtime."""
        policy = self.policy()
        scope = policy.workspace_scope()
        allowed_dir = scope.allowed_dir
        extra_read = list(scope.extra_read_dirs) if scope.extra_read_dirs else None
        self.tools.register(
            ReadFileTool(
                workspace=self.workspace,
                allowed_dir=allowed_dir,
                extra_allowed_dirs=extra_read,
            )
        )
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        for cls in (GlobTool, GrepTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(NotebookEditTool(workspace=self.workspace, allowed_dir=allowed_dir))
        if policy.exec().enabled:
            self.tools.register(self._create_exec_tool())
        if policy.web().enabled:
            self.tools.register(self._create_web_search_tool())
            self.tools.register(self._create_web_fetch_tool())
        self.tools.register(HistorySearchTool(workspace=self.workspace))
        self.tools.register(HistoryExpandTool(workspace=self.workspace))
        self.tools.register(MessageTool(send_callback=self.send_callback))
        self.tools.register(SpawnTool(manager=self.subagents))
        self.tools.register(SelfInspectTool(loop=self.loop))
        self._sync_image_gen_tool()
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone=self.timezone or "UTC")
            )

    def set_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        persona: str | None = None,
        session_key: str | None = None,
    ) -> None:
        """Update context for tools that need routing or persona info."""
        for name in ("message", "spawn", "cron", "history_search", "history_expand", "self_inspect"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    if name == "message":
                        tool.set_context(channel, chat_id, message_id)
                    elif name in ("history_search", "history_expand"):
                        tool.set_context(channel, chat_id, persona)
                    elif name == "spawn":
                        if session_key is None:
                            tool.set_context(channel, chat_id)
                        else:
                            tool.set_context(channel, chat_id, session_key)
                    elif name == "self_inspect":
                        tool.set_context(channel, chat_id, session_key, persona)
                    else:
                        tool.set_context(channel, chat_id)
        if image_tool := self.tools.get("image_gen"):
            if hasattr(image_tool, "set_persona"):
                image_tool.set_persona(persona)
