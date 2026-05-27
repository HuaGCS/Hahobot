"""Admin overview, sessions, skills, cron and command pages."""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path
from typing import Any

from aiohttp import web

from hahobot.agent.personas import (
    DEFAULT_PERSONA,
)
from hahobot.agent.skill_proposals import (
    approve_proposed_skill,
    list_proposed_skills,
    reject_proposed_skill,
)
from hahobot.agent.skills import SkillsLoader
from hahobot.agent.working_checkpoint import normalize_working_checkpoint
from hahobot.cli.session_inspector import (
    is_cli_session_key,
    list_session_summaries,
    load_session_detail,
)
from hahobot.command.catalog import CommandSpec, admin_command_specs
from hahobot.cron.types import CronJob
from hahobot.gateway.admin.base import (
    _format_duration_ms,
    _format_epoch_ms,
    _format_iso_datetime,
    _load_current_config,
    _markup,
    _page,
    _require_admin_auth,
    _runtime_workspace,
    _t,
    _th,
)
from hahobot.session.manager import SessionManager

_COMMAND_DOCS = admin_command_specs()


def _session_source_key(key: str) -> str:
    if ":" in key:
        return key.split(":", 1)[0].strip().lower()
    return "cli"


def _session_source_label(source: str) -> str:
    labels = {
        "api": "API",
        "cli": "CLI",
        "cron": "Cron",
        "discord": "Discord",
        "email": "Email",
        "feishu": "Feishu",
        "matrix": "Matrix",
        "qq": "QQ",
        "slack": "Slack",
        "telegram": "Telegram",
        "wecom": "WeCom",
        "weixin": "Weixin",
        "whatsapp": "WhatsApp",
        "websocket": "WebSocket",
    }
    return labels.get(source, source or "local")


def _working_checkpoint_view(checkpoint: dict[str, Any] | None) -> dict[str, str] | None:
    normalized = normalize_working_checkpoint(checkpoint)
    if normalized is None:
        return None
    status = normalized.get("status") or "pending"
    status_class = {
        "pending": "pill hot",
        "running": "pill hot",
        "completed": "pill",
        "blocked": "pill restart",
        "error": "pill restart",
        "interrupted": "pill restart",
    }.get(status, "pill")
    return {
        "status": status,
        "status_class": status_class,
        "goal": str(normalized.get("goal") or ""),
        "current_step": str(normalized.get("current_step") or ""),
        "next_step": str(normalized.get("next_step") or ""),
        "updated_at": _format_iso_datetime(normalized.get("updated_at")),
    }


def _collect_admin_sessions(
    request: web.Request,
    *,
    limit: int = 18,
    message_limit: int = 8,
) -> dict[str, Any]:
    workspace = _runtime_workspace(request)
    manager = SessionManager(workspace)
    visible = list_session_summaries(manager, include_internal=False)
    all_sessions = list_session_summaries(manager, include_internal=True)
    rows: list[dict[str, Any]] = []

    for summary in visible[: max(limit, 0)]:
        detail = load_session_detail(manager, summary.key, limit=message_limit)
        messages = [
            {
                "role": message.role,
                "timestamp": _format_iso_datetime(message.timestamp),
                "content": message.content,
                "tool_call_count": message.tool_call_count,
            }
            for message in (detail.messages if detail is not None else ())
        ]
        rows.append(
            {
                "key": summary.key,
                "source": _session_source_label(_session_source_key(summary.key)),
                "persona": summary.persona or DEFAULT_PERSONA,
                "preview": summary.preview or summary.key,
                "path": str(summary.path),
                "message_count": summary.message_count,
                "last_role": summary.last_role or "",
                "created_at": _format_iso_datetime(summary.created_at),
                "updated_at": _format_iso_datetime(summary.updated_at),
                "messages": messages,
                "working_checkpoint": _working_checkpoint_view(
                    detail.working_checkpoint if detail is not None else None
                ),
            }
        )

    return {
        "total_count": len(visible),
        "cli_count": sum(1 for summary in visible if is_cli_session_key(summary.key)),
        "internal_count": max(len(all_sessions) - len(visible), 0),
        "rows": rows,
    }


def _collect_admin_skills(request: web.Request) -> dict[str, Any]:
    workspace = _runtime_workspace(request)
    config = _load_current_config(request)
    disabled = set(config.agents.defaults.disabled_skills)
    loader = SkillsLoader(workspace)
    entries = sorted(
        loader.list_skills(filter_unavailable=False),
        key=lambda item: (item["source"] != "workspace", item["name"]),
    )
    available_names = {entry["name"] for entry in loader.list_skills(filter_unavailable=True)}
    always_names = set(loader.get_always_skills())
    groups: list[dict[str, Any]] = []

    for source in ("workspace", "builtin"):
        skills: list[dict[str, Any]] = []
        for entry in entries:
            if entry["source"] != source:
                continue
            meta = loader.get_skill_metadata(entry["name"]) or {}
            missing = ""
            if entry["name"] not in available_names:
                missing = loader._get_missing_requirements(loader._get_skill_meta(entry["name"]))  # noqa: SLF001
            skills.append(
                {
                    "name": entry["name"],
                    "description": meta.get("description") or entry["name"],
                    "path": entry["path"],
                    "available": entry["name"] in available_names,
                    "missing": missing,
                    "always": entry["name"] in always_names,
                    "disabled": entry["name"] in disabled,
                }
            )
        if skills:
            groups.append(
                {
                    "source": source,
                    "skills": skills,
                }
            )

    return {
        "groups": groups,
        "workspace_count": sum(1 for entry in entries if entry["source"] == "workspace"),
        "builtin_count": sum(1 for entry in entries if entry["source"] == "builtin"),
        "disabled_count": sum(1 for entry in entries if entry["name"] in disabled),
        "unavailable_count": sum(1 for entry in entries if entry["name"] not in available_names),
        "total_count": len(entries),
        "workspace_path": str(workspace / "skills"),
    }


def _load_cron_jobs(workspace: Path) -> tuple[list[CronJob], str | None, Path]:
    jobs_path = workspace / "cron" / "jobs.json"
    if not jobs_path.exists():
        return [], None, jobs_path
    try:
        payload = json.loads(jobs_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], str(exc), jobs_path
    jobs: list[CronJob] = []
    for item in payload.get("jobs", []):
        if not isinstance(item, dict):
            continue
        try:
            normalized = {
                "id": str(item["id"]),
                "name": str(item.get("name", "")),
                "enabled": bool(item.get("enabled", True)),
                "schedule": {
                    "kind": item.get("schedule", {}).get("kind", "every"),
                    "at_ms": item.get("schedule", {}).get("atMs"),
                    "every_ms": item.get("schedule", {}).get("everyMs"),
                    "expr": item.get("schedule", {}).get("expr"),
                    "tz": item.get("schedule", {}).get("tz"),
                },
                "payload": {
                    "kind": item.get("payload", {}).get("kind", "agent_turn"),
                    "message": item.get("payload", {}).get("message", ""),
                    "deliver": item.get("payload", {}).get("deliver", False),
                    "channel": item.get("payload", {}).get("channel"),
                    "to": item.get("payload", {}).get("to"),
                },
                "state": {
                    "next_run_at_ms": item.get("state", {}).get("nextRunAtMs"),
                    "last_run_at_ms": item.get("state", {}).get("lastRunAtMs"),
                    "last_status": item.get("state", {}).get("lastStatus"),
                    "last_error": item.get("state", {}).get("lastError"),
                },
                "created_at_ms": int(item.get("createdAtMs", 0) or 0),
                "updated_at_ms": int(item.get("updatedAtMs", 0) or 0),
                "delete_after_run": bool(item.get("deleteAfterRun", False)),
            }
            jobs.append(CronJob.from_dict(normalized))
        except Exception:
            continue
    return jobs, None, jobs_path


def _format_cron_schedule(request: web.Request, job: CronJob) -> str:
    schedule = job.schedule
    if schedule.kind == "every":
        return _t(
            request, "admin_cron_schedule_every", interval=_format_duration_ms(schedule.every_ms)
        )
    if schedule.kind == "at":
        return _t(request, "admin_cron_schedule_at", time=_format_epoch_ms(schedule.at_ms))
    expr = schedule.expr or "-"
    if schedule.tz:
        return _t(request, "admin_cron_schedule_expr_tz", expr=expr, tz=schedule.tz)
    return _t(request, "admin_cron_schedule_expr", expr=expr)


def _cron_status_view(request: web.Request, job: CronJob) -> tuple[str, str]:
    if not job.enabled:
        return "pill", _t(request, "admin_cron_status_disabled")
    if job.state.last_status == "error":
        return "pill restart", _t(request, "admin_cron_status_error")
    if job.state.next_run_at_ms:
        return "pill hot", _t(request, "admin_cron_status_scheduled")
    if job.state.last_status == "ok":
        return "pill", _t(request, "admin_cron_status_idle")
    return "pill", _t(request, "admin_cron_status_enabled")


def _cron_delivery_label(request: web.Request, job: CronJob) -> str:
    payload = job.payload
    if not payload.deliver:
        return _t(request, "admin_cron_delivery_local")
    if payload.channel and payload.to:
        return f"{payload.channel}/{payload.to}"
    if payload.channel:
        return payload.channel
    return _t(request, "admin_cron_delivery_remote")


def _collect_admin_cron(request: web.Request) -> dict[str, Any]:
    workspace = _runtime_workspace(request)
    jobs, error, jobs_path = _load_cron_jobs(workspace)
    rows: list[dict[str, Any]] = []
    for job in sorted(
        jobs,
        key=lambda item: (
            item.state.next_run_at_ms is None,
            item.state.next_run_at_ms or 0,
            item.name.lower(),
        ),
    ):
        status_class, status_label = _cron_status_view(request, job)
        rows.append(
            {
                "id": job.id,
                "name": job.name or job.id,
                "schedule": _format_cron_schedule(request, job),
                "status_class": status_class,
                "status_label": status_label,
                "next_run": _format_epoch_ms(job.state.next_run_at_ms),
                "last_run": _format_epoch_ms(job.state.last_run_at_ms),
                "last_error": job.state.last_error or "",
                "delivery": _cron_delivery_label(request, job),
                "prompt": job.payload.message,
            }
        )
    return {
        "rows": rows,
        "jobs_path": str(jobs_path),
        "error": error,
        "total_count": len(rows),
        "enabled_count": sum(1 for job in jobs if job.enabled),
        "error_count": sum(1 for job in jobs if job.state.last_status == "error"),
    }


async def _admin_index(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    config = _load_current_config(request)
    runtime_workspace = _runtime_workspace(request)
    config_workspace = config.workspace_path
    session_data = _collect_admin_sessions(request, limit=6, message_limit=4)
    skill_data = _collect_admin_skills(request)
    cron_data = _collect_admin_cron(request)
    provider_pool = config.agents.defaults.provider_pool
    if provider_pool and provider_pool.targets:
        provider_targets = ", ".join(target.provider for target in provider_pool.targets)
        provider_summary = (
            f"<strong><code>{escape(f'providerPool/{provider_pool.strategy}')}</code></strong>"
            f'<div class="muted"><code>{escape(provider_targets)}</code></div>'
        )
    else:
        provider_summary = (
            f"<strong><code>{escape(config.agents.defaults.provider)}</code></strong>"
        )
    mismatch = ""
    if config_workspace.resolve(strict=False) != runtime_workspace.resolve(strict=False):
        mismatch = (
            f'<div class="notice error">{_th(request, "admin_overview_workspace_mismatch")}</div>'
        )

    return _page(
        template_name="gateway/admin/overview.html",
        title=_t(request, "admin_overview_title"),
        heading=_t(request, "admin_overview_heading"),
        request=request,
        mismatch_html=_markup(mismatch),
        model_label=_t(request, "admin_label_model"),
        model_name=config.agents.defaults.model,
        provider_label=_t(request, "admin_label_provider"),
        provider_summary_html=_markup(provider_summary),
        config_workspace_label=_t(request, "admin_label_config_workspace"),
        config_workspace=str(config_workspace),
        admin_card_title=_t(request, "admin_card_admin"),
        enabled_label=_t(request, "admin_label_enabled"),
        admin_enabled_text=_t(
            request,
            "admin_boolean_true" if config.gateway.admin.enabled else "admin_boolean_false",
        ),
        auth_configured_label=_t(request, "admin_label_auth_configured"),
        auth_configured_text=_t(
            request,
            "admin_boolean_true"
            if bool(config.gateway.admin.auth_key.strip())
            else "admin_boolean_false",
        ),
        scope_label=_t(request, "admin_label_scope"),
        scope_text=_t(request, "admin_scope_text"),
        workspace_label=_t(request, "admin_meta_workspace"),
        runtime_workspace=str(runtime_workspace),
        session_count_label=_t(request, "admin_overview_sessions_label"),
        session_count=session_data["total_count"],
        skill_count_label=_t(request, "admin_overview_skills_label"),
        skill_count=skill_data["total_count"],
        cron_count_label=_t(request, "admin_overview_cron_label"),
        cron_count=cron_data["total_count"],
        config_card_title=_t(request, "admin_card_config"),
        config_card_desc_html=_markup(_th(request, "admin_card_config_desc")),
        config_card_open_label=_t(request, "admin_card_config_open"),
        sessions_card_title=_t(request, "admin_card_sessions"),
        sessions_card_desc_html=_markup(_th(request, "admin_card_sessions_desc")),
        sessions_card_open_label=_t(request, "admin_card_sessions_open"),
        skills_card_title=_t(request, "admin_card_skills"),
        skills_card_desc_html=_markup(_th(request, "admin_card_skills_desc")),
        skills_card_open_label=_t(request, "admin_card_skills_open"),
        cron_card_title=_t(request, "admin_card_cron"),
        cron_card_desc_html=_markup(_th(request, "admin_card_cron_desc")),
        cron_card_open_label=_t(request, "admin_card_cron_open"),
        weixin_card_title=_t(request, "admin_card_weixin"),
        weixin_card_desc_html=_markup(_th(request, "admin_card_weixin_desc")),
        weixin_card_open_label=_t(request, "admin_card_weixin_open"),
        personas_card_title=_t(request, "admin_card_personas"),
        personas_card_desc_html=_markup(_th(request, "admin_card_personas_desc")),
        personas_card_open_label=_t(request, "admin_card_personas_open"),
        commands_card_title=_t(request, "admin_card_commands"),
        commands_card_desc_html=_markup(_th(request, "admin_card_commands_desc")),
        commands_card_open_label=_t(request, "admin_card_commands_open"),
        recent_sessions_title=_t(request, "admin_overview_recent_sessions_title"),
        recent_sessions_desc=_t(request, "admin_overview_recent_sessions_desc"),
        recent_sessions=session_data["rows"],
    )


async def _admin_sessions_page(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    session_data = _collect_admin_sessions(request)
    return _page(
        template_name="gateway/admin/sessions.html",
        title=_t(request, "admin_sessions_title"),
        heading=_t(request, "admin_sessions_heading"),
        request=request,
        sessions_nav_label=_t(request, "admin_nav_sessions"),
        sessions_intro_html=_markup(_th(request, "admin_sessions_intro")),
        sessions_title=_t(request, "admin_sessions_title"),
        total_label=_t(request, "admin_sessions_total_label"),
        cli_label=_t(request, "admin_sessions_cli_label"),
        internal_label=_t(request, "admin_sessions_internal_label"),
        total_count=session_data["total_count"],
        cli_count=session_data["cli_count"],
        internal_count=session_data["internal_count"],
        empty_label=_t(request, "admin_sessions_empty"),
        persona_label=_t(request, "admin_sessions_persona_label"),
        updated_label=_t(request, "admin_sessions_updated_label"),
        created_label=_t(request, "admin_sessions_created_label"),
        path_label=_t(request, "admin_sessions_path_label"),
        source_label=_t(request, "admin_sessions_source_label"),
        messages_label=_t(request, "admin_sessions_messages_label"),
        checkpoint_label=_t(request, "admin_sessions_checkpoint_label"),
        checkpoint_status_label=_t(request, "admin_sessions_checkpoint_status_label"),
        checkpoint_goal_label=_t(request, "admin_sessions_checkpoint_goal_label"),
        checkpoint_current_label=_t(request, "admin_sessions_checkpoint_current_label"),
        checkpoint_next_label=_t(request, "admin_sessions_checkpoint_next_label"),
        checkpoint_updated_label=_t(request, "admin_sessions_checkpoint_updated_label"),
        sessions=session_data["rows"],
    )


async def _admin_skills_page(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    skill_data = _collect_admin_skills(request)
    groups = []
    for group in skill_data["groups"]:
        source_key = (
            "admin_skills_source_workspace"
            if group["source"] == "workspace"
            else "admin_skills_source_builtin"
        )
        groups.append({**group, "title": _t(request, source_key)})
    workspace = _runtime_workspace(request)
    proposals = [
        {
            "name": p.name,
            "description": p.description,
            "preview": p.body_preview,
            "path": str(p.path),
        }
        for p in list_proposed_skills(workspace)
    ]
    flash = None
    error = None
    approved = request.query.get("proposed_approved")
    rejected = request.query.get("proposed_rejected")
    failed = request.query.get("proposed_error")
    if approved:
        flash = _t(request, "admin_skills_proposal_approved", name=approved)
    elif rejected:
        flash = _t(request, "admin_skills_proposal_rejected", name=rejected)
    if failed:
        error = _t(request, "admin_skills_proposal_failed", reason=failed)
    return _page(
        template_name="gateway/admin/skills.html",
        title=_t(request, "admin_skills_title"),
        heading=_t(request, "admin_skills_heading"),
        request=request,
        flash=flash,
        error=error,
        skills_nav_label=_t(request, "admin_nav_skills"),
        skills_intro_html=_markup(
            _th(request, "admin_skills_intro", path=skill_data["workspace_path"])
        ),
        total_label=_t(request, "admin_skills_total_label"),
        workspace_label=_t(request, "admin_skills_workspace_label"),
        builtin_label=_t(request, "admin_skills_builtin_label"),
        disabled_label=_t(request, "admin_skills_disabled_label"),
        unavailable_label=_t(request, "admin_skills_unavailable_label"),
        total_count=skill_data["total_count"],
        workspace_count=skill_data["workspace_count"],
        builtin_count=skill_data["builtin_count"],
        disabled_count=skill_data["disabled_count"],
        unavailable_count=skill_data["unavailable_count"],
        ready_label=_t(request, "admin_skills_ready_label"),
        missing_label=_t(request, "admin_skills_missing_label"),
        always_label=_t(request, "admin_skills_always_label"),
        hidden_label=_t(request, "admin_skills_hidden_label"),
        no_skills_label=_t(request, "admin_skills_empty"),
        proposed_title=_t(request, "admin_skills_proposed_title"),
        proposed_desc=_t(request, "admin_skills_proposed_desc"),
        proposed_empty=_t(request, "admin_skills_proposed_empty"),
        proposed_approve_label=_t(request, "admin_skills_proposed_approve"),
        proposed_reject_label=_t(request, "admin_skills_proposed_reject"),
        proposals=proposals,
        groups=groups,
    )


async def _admin_skill_proposal_approve(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    name = request.match_info.get("name", "")
    workspace = _runtime_workspace(request)
    try:
        approve_proposed_skill(workspace, name)
    except ValueError as exc:
        from urllib.parse import quote

        raise web.HTTPFound(f"/admin/skills?proposed_error={quote(str(exc), safe='')}") from None
    raise web.HTTPFound(f"/admin/skills?proposed_approved={name}")


async def _admin_skill_proposal_reject(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    name = request.match_info.get("name", "")
    workspace = _runtime_workspace(request)
    try:
        reject_proposed_skill(workspace, name)
    except ValueError as exc:
        from urllib.parse import quote

        raise web.HTTPFound(f"/admin/skills?proposed_error={quote(str(exc), safe='')}") from None
    raise web.HTTPFound(f"/admin/skills?proposed_rejected={name}")


async def _admin_cron_page(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    cron_data = _collect_admin_cron(request)
    return _page(
        template_name="gateway/admin/cron.html",
        title=_t(request, "admin_cron_title"),
        heading=_t(request, "admin_cron_heading"),
        request=request,
        flash=None,
        error=cron_data["error"],
        cron_nav_label=_t(request, "admin_nav_cron"),
        cron_intro_html=_markup(_th(request, "admin_cron_intro", path=cron_data["jobs_path"])),
        total_label=_t(request, "admin_cron_total_label"),
        enabled_label=_t(request, "admin_cron_enabled_label"),
        error_label=_t(request, "admin_cron_error_label"),
        total_count=cron_data["total_count"],
        enabled_count=cron_data["enabled_count"],
        error_count=cron_data["error_count"],
        empty_label=_t(request, "admin_cron_empty"),
        schedule_label=_t(request, "admin_cron_schedule_label"),
        next_run_label=_t(request, "admin_cron_next_run_label"),
        last_run_label=_t(request, "admin_cron_last_run_label"),
        delivery_label=_t(request, "admin_cron_delivery_label"),
        prompt_label=_t(request, "admin_cron_prompt_label"),
        rows=cron_data["rows"],
    )


def _command_usage_lines(request: web.Request, spec: CommandSpec) -> list[str]:
    if spec.usage_text_key:
        return [
            line.strip()
            for line in _t(request, spec.usage_text_key).splitlines()
            if line.strip().startswith("/")
        ]
    return list(spec.usage_lines)


def _command_panel_id(spec: CommandSpec) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", spec.command.lower()).strip("-")
    return f"command-{slug or 'item'}"


def _render_command_nav_item(request: web.Request, spec: CommandSpec, *, active: bool) -> str:
    panel_id = _command_panel_id(spec)
    preview = _t(request, spec.description_keys[0]) if spec.description_keys else spec.command
    css_class = "command-nav-item active" if active else "command-nav-item"
    selected = "true" if active else "false"
    return (
        f'<a class="{css_class}" href="#{panel_id}" data-command-target="{panel_id}" '
        f'role="tab" aria-selected="{selected}" aria-controls="{panel_id}">'
        f"<code>{escape(spec.command)}</code>"
        f'<span class="command-nav-preview">{escape(preview)}</span>'
        "</a>"
    )


def _render_command_panel(request: web.Request, spec: CommandSpec, *, active: bool) -> str:
    description_items = "".join(
        f"<li>{escape(_t(request, key))}</li>" for key in spec.description_keys
    )
    usage_lines = "\n".join(_command_usage_lines(request, spec))
    panel_id = _command_panel_id(spec)
    aliases = ""
    if spec.aliases:
        aliases_html = " ".join(f"<code>{escape(alias)}</code>" for alias in spec.aliases)
        aliases = (
            f"<div><strong>{escape(_t(request, 'admin_commands_aliases_label'))}:</strong> "
            f"{aliases_html}</div>"
        )
    notes = ""
    if spec.note_key:
        notes = (
            f"<div><strong>{escape(_t(request, 'admin_commands_notes_label'))}:</strong> "
            f"{_th(request, spec.note_key)}</div>"
        )
    active_class = " active" if active else ""
    hidden = "" if active else " hidden"
    return f"""
      <section id="{panel_id}" class="card stack command-panel{active_class}" data-command-panel="{panel_id}" role="tabpanel"{hidden}>
        <div class="section-head">
          <h2><code>{escape(spec.command)}</code></h2>
        </div>
        <div class="stack">
          <div><strong>{escape(_t(request, "admin_commands_forms_label"))}:</strong></div>
          <ul class="detail-list">{description_items}</ul>
          <div><strong>{escape(_t(request, "admin_commands_usage_label"))}:</strong></div>
          <pre class="code-block"><code>{escape(usage_lines)}</code></pre>
          {aliases}
          {notes}
        </div>
      </section>
    """


async def _admin_commands_page(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    nav_items = "".join(
        _render_command_nav_item(request, spec, active=index == 0)
        for index, spec in enumerate(_COMMAND_DOCS)
    )
    panels = "".join(
        _render_command_panel(request, spec, active=index == 0)
        for index, spec in enumerate(_COMMAND_DOCS)
    )
    return _page(
        template_name="gateway/admin/commands.html",
        title=_t(request, "admin_commands_title"),
        heading=_t(request, "admin_commands_heading"),
        request=request,
        commands_nav_label=_t(request, "admin_nav_commands"),
        commands_intro_html=_markup(_th(request, "admin_commands_intro")),
        commands_title=_t(request, "admin_commands_title"),
        command_count=len(_COMMAND_DOCS),
        aliases_label=_t(request, "admin_commands_aliases_label"),
        alias_count=sum(len(spec.aliases) for spec in _COMMAND_DOCS),
        list_title=_t(request, "admin_commands_list_title"),
        list_desc=_t(request, "admin_commands_list_desc"),
        nav_items_html=_markup(nav_items),
        panels_html=_markup(panels),
    )
