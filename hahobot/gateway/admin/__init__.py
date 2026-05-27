"""Built-in admin UI for per-instance config and persona editing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import web

from hahobot.agent.tools.image_gen import ImageGenTool
from hahobot.gateway.admin.base import _admin_login_page, _admin_login_submit, _admin_logout
from hahobot.gateway.admin.config_view import (
    _admin_config_page,
    _admin_config_submit,
    _admin_memory_migrate_legacy,
)
from hahobot.gateway.admin.constants import (
    _ADMIN_CONFIG_PATH_KEY,
    _ADMIN_RELOAD_RUNTIME_KEY,
    _ADMIN_SUBAGENT_MANAGER_KEY,
    _ADMIN_WEIXIN_LOGIN_SESSIONS_KEY,
    _ADMIN_WORKSPACE_KEY,
)
from hahobot.gateway.admin.dashboard import (
    _admin_commands_page,
    _admin_cron_page,
    _admin_index,
    _admin_sessions_page,
    _admin_skill_proposal_approve,
    _admin_skill_proposal_reject,
    _admin_skills_page,
)
from hahobot.gateway.admin.personas import (
    _admin_persona_create,
    _admin_persona_migrate_user,
    _admin_persona_page,
    _admin_persona_scene_preview,
    _admin_persona_scene_template_save,
    _admin_persona_submit,
    _admin_personas_page,
)
from hahobot.gateway.admin.subagents import (
    _admin_subagent_cancel,
    _admin_subagent_inject,
    _admin_subagents_page,
)
from hahobot.gateway.admin.weixin import (
    WeixinAdminLoginSession,
    _admin_weixin_cancel,
    _admin_weixin_page,
    _admin_weixin_start,
)

__all__ = [
    "register_admin_routes",
    "update_admin_runtime_workspace",
    "WeixinAdminLoginSession",
    "ImageGenTool",
]


def register_admin_routes(
    app: web.Application,
    *,
    config_path: Path,
    workspace: Path,
    reload_runtime: Callable[[], Awaitable[None]] | None = None,
    subagent_manager: object | None = None,
) -> None:
    """Register built-in admin routes for the current gateway instance."""
    app[_ADMIN_CONFIG_PATH_KEY] = config_path
    app[_ADMIN_WORKSPACE_KEY] = workspace
    app[_ADMIN_WEIXIN_LOGIN_SESSIONS_KEY] = {}
    if reload_runtime is not None:
        app[_ADMIN_RELOAD_RUNTIME_KEY] = reload_runtime
    if subagent_manager is not None:
        app[_ADMIN_SUBAGENT_MANAGER_KEY] = subagent_manager
    app.router.add_get("/admin", _admin_index)
    app.router.add_get("/admin/login", _admin_login_page)
    app.router.add_post("/admin/login", _admin_login_submit)
    app.router.add_post("/admin/logout", _admin_logout)
    app.router.add_get("/admin/config", _admin_config_page)
    app.router.add_post("/admin/config", _admin_config_submit)
    app.router.add_post("/admin/memory/migrate-legacy", _admin_memory_migrate_legacy)
    app.router.add_get("/admin/sessions", _admin_sessions_page)
    app.router.add_get("/admin/skills", _admin_skills_page)
    app.router.add_post(
        "/admin/skills/proposed/{name:[A-Za-z0-9][A-Za-z0-9._-]{0,63}}/approve",
        _admin_skill_proposal_approve,
    )
    app.router.add_post(
        "/admin/skills/proposed/{name:[A-Za-z0-9][A-Za-z0-9._-]{0,63}}/reject",
        _admin_skill_proposal_reject,
    )
    app.router.add_get("/admin/cron", _admin_cron_page)
    app.router.add_get("/admin/subagents", _admin_subagents_page)
    app.router.add_post(
        "/admin/subagents/{task_id:[A-Za-z0-9]+}/inject",
        _admin_subagent_inject,
    )
    app.router.add_post(
        "/admin/subagents/{task_id:[A-Za-z0-9]+}/cancel",
        _admin_subagent_cancel,
    )
    app.router.add_get("/admin/weixin", _admin_weixin_page)
    app.router.add_post("/admin/weixin/start", _admin_weixin_start)
    app.router.add_post("/admin/weixin/cancel", _admin_weixin_cancel)
    app.router.add_get("/admin/commands", _admin_commands_page)
    app.router.add_get("/admin/personas", _admin_personas_page)
    app.router.add_post("/admin/personas/new", _admin_persona_create)
    app.router.add_get("/admin/personas/{persona:[A-Za-z0-9_-]+}", _admin_persona_page)
    app.router.add_post(
        "/admin/personas/{persona:[A-Za-z0-9_-]+}/scene-preview", _admin_persona_scene_preview
    )
    app.router.add_post(
        "/admin/personas/{persona:[A-Za-z0-9_-]+}/scene-template-save",
        _admin_persona_scene_template_save,
    )
    app.router.add_post(
        "/admin/personas/{persona:[A-Za-z0-9_-]+}/migrate-user", _admin_persona_migrate_user
    )
    app.router.add_post("/admin/personas/{persona:[A-Za-z0-9_-]+}", _admin_persona_submit)


def update_admin_runtime_workspace(app: web.Application, workspace: Path) -> None:
    """Update the runtime-workspace pointer used by the admin UI."""
    app[_ADMIN_WORKSPACE_KEY] = workspace
