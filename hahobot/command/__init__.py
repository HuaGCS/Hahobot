"""Slash command routing and built-in handlers."""

from __future__ import annotations

from hahobot.command.router import CommandContext, CommandRouter

__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]


def __getattr__(name: str):
    """Lazily expose heavier built-in command registration."""
    if name == "register_builtin_commands":
        from hahobot.command.builtin import register_builtin_commands

        return register_builtin_commands
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
