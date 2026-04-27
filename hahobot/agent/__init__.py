"""Agent core module."""

from __future__ import annotations

from hahobot.agent.context import ContextBuilder
from hahobot.agent.hook import AgentHook, AgentHookContext, CompositeHook
from hahobot.agent.hook_bridge import (
    ExternalHookBridge,
    ExternalHookBridgeBlocked,
    ExternalHookBridgeBlockedError,
    ExternalHookBridgeError,
)

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


def __getattr__(name: str):
    """Lazily expose heavyweight agent classes without creating import cycles."""
    if name == "AgentLoop":
        from hahobot.agent.loop import AgentLoop

        return AgentLoop
    if name in {"Dream", "MemoryStore"}:
        from hahobot.agent.memory import Dream, MemoryStore

        return {"Dream": Dream, "MemoryStore": MemoryStore}[name]
    if name == "SkillsLoader":
        from hahobot.agent.skills import SkillsLoader

        return SkillsLoader
    if name == "SubagentManager":
        from hahobot.agent.subagent import SubagentManager

        return SubagentManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
