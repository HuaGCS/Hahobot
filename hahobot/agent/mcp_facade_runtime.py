"""MCP-facing compatibility helpers for AgentLoop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hahobot.agent.mcp_runtime import MCPRuntime

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.session.manager import Session


class MCPFacadeRuntimeManager:
    """Own MCP compatibility helpers that remain on AgentLoop for callers/tests."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    def remove_registered_mcp_tools(self) -> None:
        """Remove all dynamically registered MCP tools from the registry."""
        self.loop._mcp_runtime.remove_registered_tools()

    @staticmethod
    def dump_mcp_servers(servers: dict) -> dict:
        """Normalize MCP server config for value-based comparisons."""
        return MCPRuntime.dump_servers(servers)

    def mcp_tool_candidates(self, suffix: str) -> list[str]:
        """Return MCP tool names matching a logical tool suffix."""
        return self.loop._mcp_runtime.tool_candidates(suffix)

    def preferred_mcp_tool(self, suffix: str) -> str | None:
        """Pick the most likely MCP tool wrapper for a logical tool name."""
        return self.loop._mcp_runtime.preferred_tool(suffix)

    def has_memorix_tools(self) -> bool:
        """Return whether a Memorix MCP server is currently available."""
        return self.loop._mcp_runtime.has_memorix_tools()

    def runtime_skill_names(self) -> list[str]:
        """Return runtime-activated skills inferred from connected tools."""
        skill_names: list[str] = []
        if self.has_memorix_tools() and "memorix" not in set(self.loop._disabled_skills):
            skill_names.append("memorix")
        return skill_names

    async def maybe_start_memorix_session(self, session: Session) -> str:
        """Bind the current workspace to Memorix once per MCP connection and session."""
        return await self.loop._mcp_runtime.maybe_start_memorix_session(session)

    async def reset_connections(self) -> None:
        """Drop MCP tool registrations and close active MCP connections."""
        await self.loop._mcp_runtime.reset_connections()
