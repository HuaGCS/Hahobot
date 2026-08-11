"""Shared temporary-WebUI session identity helpers."""

from __future__ import annotations

TEMPORARY_WEBUI_SESSION_PREFIX = "webui:__temporary__-"
TEMPORARY_WEBUI_CHAT_PREFIX = "__temporary__-"


def is_temporary_session_key(key: str) -> bool:
    """Return whether *key* belongs to the in-memory-only WebUI namespace."""
    return isinstance(key, str) and key.startswith(TEMPORARY_WEBUI_SESSION_PREFIX)


def is_temporary_webui_target(channel: str, chat_id: str) -> bool:
    """Return whether a channel/chat pair targets a temporary WebUI conversation."""
    return (
        channel == "webui"
        and isinstance(chat_id, str)
        and chat_id.startswith(TEMPORARY_WEBUI_CHAT_PREFIX)
    )
