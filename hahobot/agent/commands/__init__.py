"""Command handlers for AgentLoop slash commands."""

from hahobot.agent.commands.language import LanguageCommandHandler
from hahobot.agent.commands.mcp import MCPCommandHandler
from hahobot.agent.commands.persona import PersonaCommandHandler
from hahobot.agent.commands.preset import PresetCommandHandler
from hahobot.agent.commands.router import build_agent_command_router
from hahobot.agent.commands.scene import SceneCommandHandler
from hahobot.agent.commands.skill import SkillCommandHandler
from hahobot.agent.commands.stchar import STCharCommandHandler
from hahobot.agent.commands.system import SystemCommandHandler

__all__ = [
    "LanguageCommandHandler",
    "MCPCommandHandler",
    "PersonaCommandHandler",
    "PresetCommandHandler",
    "SceneCommandHandler",
    "SkillCommandHandler",
    "STCharCommandHandler",
    "SystemCommandHandler",
    "build_agent_command_router",
]
