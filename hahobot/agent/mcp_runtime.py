"""MCP runtime state and connection management."""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from hahobot.session.manager import Session


class MCPRuntime:
    """Manage MCP server config, connection lifecycle, and Memorix startup state."""

    def __init__(
        self,
        *,
        tools,
        workspace: Path,
        servers: dict | None = None,
        memorix_context_max_chars: int = 4_000,
        truncate_prompt_text: Callable[[str, int], str],
    ) -> None:
        self.tools = tools
        self.workspace = workspace
        self.servers = servers or {}
        self.stack: AsyncExitStack | None = None
        self.connected = False
        self.connecting = False
        self.connection_epoch = 0
        self._memorix_context_max_chars = memorix_context_max_chars
        self._truncate_prompt_text = truncate_prompt_text
        self._memorix_started_sessions: set[tuple[int, str, str, str]] = set()
        self._memorix_session_context: dict[tuple[int, str, str, str], str] = {}

    def update_runtime(
        self,
        *,
        workspace: Path | None = None,
        servers: dict | None = None,
    ) -> None:
        """Refresh runtime-bound workspace and optionally server config."""
        if workspace is not None:
            self.workspace = workspace
        if servers is not None:
            self.servers = servers

    def remove_registered_tools(self) -> None:
        """Remove all dynamically registered MCP tools from the registry."""
        for tool_name in list(self.tools.tool_names):
            if tool_name.startswith("mcp_"):
                self.tools.unregister(tool_name)

    @staticmethod
    def dump_servers(servers: dict) -> dict:
        """Normalize MCP server config for value-based comparisons."""
        dumped = {}
        for name, cfg in servers.items():
            dumped[name] = cfg.model_dump() if hasattr(cfg, "model_dump") else cfg
        return dumped

    def tool_candidates(self, suffix: str) -> list[str]:
        """Return MCP tool names matching a logical tool suffix."""
        expected = f"_{suffix}"
        return sorted(
            name
            for name in self.tools.tool_names
            if name.startswith("mcp_") and name.endswith(expected)
        )

    def preferred_tool(self, suffix: str) -> str | None:
        """Pick the most likely MCP tool wrapper for a logical tool name."""
        candidates = self.tool_candidates(suffix)
        if not candidates:
            return None
        for name in candidates:
            if name.startswith("mcp_memorix_"):
                return name
        return candidates[0]

    def has_memorix_tools(self) -> bool:
        """Return whether a Memorix MCP server is currently available."""
        for suffix in (
            "memorix_session_start",
            "memorix_search",
            "memorix_detail",
            "memorix_store",
            "memorix_store_reasoning",
        ):
            if self.preferred_tool(suffix):
                return True
        return False

    @staticmethod
    def _tool_result_to_text(result: Any) -> str:
        """Collapse a tool result into plain text for prompt injection."""
        if result is None:
            return ""
        if isinstance(result, str):
            return result
        if isinstance(result, list):
            parts: list[str] = []
            for block in result:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(str(block))
            return "\n".join(part for part in parts if part)
        return str(result)

    @staticmethod
    def _is_usable_memorix_context(text: str) -> bool:
        """Filter placeholder MCP outputs that should not enter the prompt."""
        stripped = text.strip()
        if not stripped or stripped == "(no output)":
            return False
        if stripped.startswith("Error"):
            return False
        if stripped.startswith("(MCP tool call"):
            return False
        return True

    def _truncate_memorix_context(self, text: str) -> str:
        """Keep injected Memorix startup context bounded."""
        stripped = text.strip()
        if len(stripped) <= self._memorix_context_max_chars:
            return stripped
        return self._truncate_prompt_text(stripped, self._memorix_context_max_chars)

    async def maybe_start_memorix_session(self, session: Session) -> str:
        """Bind the current workspace to Memorix once per MCP connection and session."""
        tool_name = self.preferred_tool("memorix_session_start")
        if not self.connected or not tool_name:
            return ""

        project_root = str(self.workspace.expanduser().resolve(strict=False))
        state_key = (self.connection_epoch, session.key, project_root, tool_name)
        cached = self._memorix_session_context.get(state_key)
        if cached is not None:
            return cached
        if state_key in self._memorix_started_sessions:
            return ""

        result = await self.tools.execute(
            tool_name,
            {
                "agent": "hahobot",
                "projectRoot": project_root,
                "sessionId": session.key,
            },
        )
        rendered = self._tool_result_to_text(result)
        self._memorix_started_sessions.add(state_key)
        if not self._is_usable_memorix_context(rendered):
            if rendered.strip():
                logger.warning("Memorix session start returned non-context output: {}", rendered[:200])
            return ""

        context = self._truncate_memorix_context(rendered)
        self._memorix_session_context[state_key] = context
        return context

    async def reset_connections(self) -> None:
        """Drop MCP tool registrations and close active MCP connections."""
        self.remove_registered_tools()
        if self.stack:
            try:
                await self.stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass
            self.stack = None
        self.connected = False
        self.connecting = False
        self._memorix_started_sessions.clear()
        self._memorix_session_context.clear()

    async def connect(self) -> None:
        """Connect to configured MCP servers once, lazily."""
        if self.connected or self.connecting or not self.servers:
            return
        self.connecting = True
        from hahobot.agent.tools.mcp import connect_mcp_servers

        try:
            self.stack = AsyncExitStack()
            await self.stack.__aenter__()
            await connect_mcp_servers(self.servers, self.tools, self.stack)
            self.connected = True
            self.connection_epoch += 1
        except BaseException as exc:
            logger.error("Failed to connect MCP servers (will retry next message): {}", exc)
            if self.stack:
                try:
                    await self.stack.aclose()
                except Exception:
                    pass
                self.stack = None
        finally:
            self.connecting = False
