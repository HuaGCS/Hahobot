"""Admin endpoints for live HOTS (Human on the Swarm) intervention.

Surfaces the currently running subagents, lets an admin push a system message
into a specific task (delivered before the next LLM call) and cancel a task.
The live ``SubagentManager`` reference is attached to the aiohttp app by
``register_admin_routes`` — when missing (e.g. the gateway is started without
an attached AgentLoop) the pages render a friendly "no live runtime" state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from hahobot.gateway.admin.base import (
    _markup,
    _page,
    _redirect,
    _require_admin_auth,
    _t,
    _th,
)
from hahobot.gateway.admin.constants import _ADMIN_SUBAGENT_MANAGER_KEY

if TYPE_CHECKING:
    from hahobot.agent.subagent import SubagentManager


def _subagent_manager(request: web.Request) -> SubagentManager | None:
    return request.app.get(_ADMIN_SUBAGENT_MANAGER_KEY)


async def _admin_subagents_page(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    manager = _subagent_manager(request)
    flash = None
    error = None
    if request.query.get("injected"):
        flash = _t(request, "admin_subagents_inject_success", task_id=request.query["injected"])
    if request.query.get("cancelled"):
        flash = _t(request, "admin_subagents_cancel_success", task_id=request.query["cancelled"])
    if request.query.get("error"):
        error = _t(request, "admin_subagents_action_failed", reason=request.query["error"])

    if manager is None:
        rows: list[dict[str, object]] = []
        intro_extra = _t(request, "admin_subagents_no_runtime")
    else:
        snapshot = manager.running_tasks_snapshot()
        rows = []
        for entry in snapshot:
            task_id = str(entry.get("task_id", ""))
            rows.append(
                {
                    **entry,
                    "pending_injections": manager.pending_injections(task_id),
                }
            )
        intro_extra = ""

    return _page(
        template_name="gateway/admin/subagents.html",
        title=_t(request, "admin_subagents_title"),
        heading=_t(request, "admin_subagents_heading"),
        request=request,
        flash=flash,
        error=error,
        subagents_nav_label=_t(request, "admin_nav_subagents"),
        subagents_intro_html=_markup(_th(request, "admin_subagents_intro")),
        intro_extra_label=intro_extra,
        empty_label=_t(request, "admin_subagents_empty"),
        col_task_label=_t(request, "admin_subagents_col_task"),
        col_session_label=_t(request, "admin_subagents_col_session"),
        col_model_label=_t(request, "admin_subagents_col_model"),
        col_mode_label=_t(request, "admin_subagents_col_mode"),
        col_inject_label=_t(request, "admin_subagents_col_inject"),
        col_pending_label=_t(request, "admin_subagents_col_pending"),
        col_actions_label=_t(request, "admin_subagents_col_actions"),
        inject_placeholder=_t(request, "admin_subagents_inject_placeholder"),
        inject_button_label=_t(request, "admin_subagents_inject_button"),
        cancel_button_label=_t(request, "admin_subagents_cancel_button"),
        cancel_confirm_label=_t(request, "admin_subagents_cancel_confirm"),
        rows=rows,
    )


async def _admin_subagent_inject(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    task_id = request.match_info.get("task_id", "")
    manager = _subagent_manager(request)
    if manager is None:
        raise _redirect(request, "/admin/subagents?error=no_runtime")
    form = await request.post()
    content = str(form.get("content", "")).strip()
    if not content:
        raise _redirect(request, "/admin/subagents?error=empty_message")
    ok = manager.inject_message(task_id, content)
    if not ok:
        raise _redirect(request, "/admin/subagents?error=task_not_running")
    raise _redirect(request, f"/admin/subagents?injected={task_id}")


async def _admin_subagent_cancel(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    task_id = request.match_info.get("task_id", "")
    manager = _subagent_manager(request)
    if manager is None:
        raise _redirect(request, "/admin/subagents?error=no_runtime")
    ok = await manager.cancel_task(task_id)
    if not ok:
        raise _redirect(request, "/admin/subagents?error=task_not_running")
    raise _redirect(request, f"/admin/subagents?cancelled={task_id}")
