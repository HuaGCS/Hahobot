"""Server-rendered chat WebUI (nanobot-style) that folds admin into a Settings area.

Auth is shared with the admin surface: the WebUI is only reachable when
``gateway.admin`` is enabled with an ``authKey`` and the admin login session is
present. ``gateway.webui.enabled`` gates the chat surface itself. Everything runs
in the same aiohttp/Jinja gateway runtime — there is no standalone SPA.
"""

from __future__ import annotations

from hahobot.gateway.webui.app import (
    _WEBUI_AGENT_KEY,
    _WEBUI_SESSION_MANAGER_KEY,
    register_webui_routes,
)

__all__ = [
    "register_webui_routes",
    "_WEBUI_AGENT_KEY",
    "_WEBUI_SESSION_MANAGER_KEY",
]
