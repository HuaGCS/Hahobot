"""Command handlers for AgentLoop slash commands."""

from hahobot.agent.commands.language import LanguageCommandHandler
from hahobot.agent.commands.mcp import MCPCommandHandler
from hahobot.agent.commands.persona import PersonaCommandHandler
from hahobot.agent.commands.router import build_agent_command_router
from hahobot.agent.commands.skill import SkillCommandHandler
from hahobot.agent.commands.system import SystemCommandHandler

__all__ = [
    "LanguageCommandHandler",
    "MCPCommandHandler",
    "PersonaCommandHandler",
    "SkillCommandHandler",
    "SystemCommandHandler",
    "build_agent_command_router",
]
