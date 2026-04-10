"""Agent core module."""

from hahobot.agent.context import ContextBuilder
from hahobot.agent.hook import AgentHook, AgentHookContext, CompositeHook
from hahobot.agent.hook_bridge import (
    ExternalHookBridge,
    ExternalHookBridgeBlocked,
    ExternalHookBridgeBlockedError,
    ExternalHookBridgeError,
)
from hahobot.agent.loop import AgentLoop
from hahobot.agent.memory import Dream, MemoryStore
from hahobot.agent.skills import SkillsLoader
from hahobot.agent.subagent import SubagentManager

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "Dream",
    "ExternalHookBridge",
    "ExternalHookBridgeBlocked",
    "ExternalHookBridgeBlockedError",
    "ExternalHookBridgeError",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
]
