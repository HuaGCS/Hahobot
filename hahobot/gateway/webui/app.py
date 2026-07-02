"""Routes and chat WebSocket for the server-rendered WebUI.

The WebUI reuses the admin login session (see ``hahobot.gateway.admin.base``) and
the running ``AgentLoop`` / ``SessionManager`` injected by the gateway command. It
drives chat turns through ``agent.process_direct(..., on_stream=..., on_progress=...)``
and reuses the ``ready`` / ``delta`` / ``stream_end`` frame shape from the WebSocket
channel so behavior stays consistent across surfaces.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import WSMsgType, web
from loguru import logger

from hahobot.agent.memory_metadata import load_persona_memory_layer_status
from hahobot.agent.personas import DEFAULT_PERSONA, list_personas
from hahobot.agent.working_checkpoint import normalize_working_checkpoint
from hahobot.gateway.admin.base import (
    _admin_language,
    _current_config_path,
    _is_authenticated,
    _language_switch,
    _load_current_config,
    _markup,
    _redirect,
    _runtime_workspace,
    _set_lang_cookie,
    _t,
)
from hahobot.gateway.webui.broadcast import WebUIBroadcaster, WebUIConnection
from hahobot.utils.html_templates import render_html_template

_WEBUI_AGENT_KEY = web.AppKey("webui_agent", object)
_WEBUI_SESSION_MANAGER_KEY = web.AppKey("webui_session_manager", object)
_WEBUI_BROADCASTER_KEY = web.AppKey("webui_broadcaster", object)
_WEBUI_CRON_KEY = web.AppKey("webui_cron_service", object)

_WEBUI_SESSION_PREFIX = "webui:"
_DEFAULT_WEBUI_SESSION = "webui:default"
_MAX_WS_MSG_BYTES = 4 * 1024 * 1024
_SESSION_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


# --- config / auth guards -------------------------------------------------


def _webui_enabled(request: web.Request) -> bool:
    try:
        cfg = _load_current_config(request)
        return bool(cfg.gateway.webui.enabled and cfg.gateway.admin.enabled)
    except Exception:
        return False


def _require_webui(request: web.Request) -> None:
    if not _webui_enabled(request):
        raise web.HTTPNotFound()


def _require_webui_auth(request: web.Request) -> None:
    _require_webui(request)
    if _is_authenticated(request):
        return
    destination = quote(str(request.rel_url), safe="/?=&")
    raise _redirect(request, f"/admin/login?next={destination}")


def _agent(request: web.Request):
    return request.app.get(_WEBUI_AGENT_KEY)


def _session_manager(request: web.Request):
    return request.app.get(_WEBUI_SESSION_MANAGER_KEY)


def _broadcaster(request: web.Request) -> WebUIBroadcaster | None:
    value = request.app.get(_WEBUI_BROADCASTER_KEY)
    return value if isinstance(value, WebUIBroadcaster) else None


def _cron_service(request: web.Request):
    return request.app.get(_WEBUI_CRON_KEY)


# --- session helpers ------------------------------------------------------


def _normalize_session_key(raw: str | None) -> str:
    """Coerce any input to a safe ``webui:<id>`` key.

    Chat is restricted to ``webui:*`` sessions so a WebUI message can never be
    injected into a live channel session (e.g. a Telegram conversation).
    """
    value = (raw or "").strip()
    if not value:
        return _DEFAULT_WEBUI_SESSION
    ident = value.split(":", 1)[1] if ":" in value else value
    ident = _SESSION_ID_RE.sub("-", ident).strip("-") or "default"
    return f"{_WEBUI_SESSION_PREFIX}{ident}"


def _list_webui_sessions(request: web.Request) -> list[dict[str, Any]]:
    sm = _session_manager(request)
    if sm is None:
        return []
    items = [
        s for s in sm.list_sessions() if str(s.get("key", "")).startswith(_WEBUI_SESSION_PREFIX)
    ]
    items.sort(key=lambda s: str(s.get("updated_at") or ""), reverse=True)
    return items


def _conversation_messages(request: web.Request, session_key: str) -> list[dict[str, str]]:
    sm = _session_manager(request)
    if sm is None:
        return []
    session = sm.get_or_create(session_key)
    out: list[dict[str, str]] = []
    for message in session.messages:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        content = (message.get("content") or "").strip()
        if not content:
            continue
        out.append(
            {"role": role, "content": content, "timestamp": str(message.get("timestamp") or "")}
        )
    return out


def _current_persona(request: web.Request, session_key: str) -> str:
    sm = _session_manager(request)
    if sm is None:
        return DEFAULT_PERSONA
    session = sm.get_or_create(session_key)
    return session.metadata.get("persona") or DEFAULT_PERSONA


def _working_checkpoint(request: web.Request, session_key: str) -> dict[str, Any] | None:
    sm = _session_manager(request)
    if sm is None:
        return None
    session = sm.get_or_create(session_key)
    return normalize_working_checkpoint(session.metadata.get("working_checkpoint"))


# --- media (delivery artifacts under workspace/out) ------------------------


def _media_root(request: web.Request) -> Path:
    return _runtime_workspace(request) / "out"


def _media_url_for(raw: str, root: Path) -> str | None:
    """Map an OutboundMessage media entry to a browser-fetchable URL.

    Remote URLs pass through; local files are exposed only when they live under
    ``workspace/out`` (the delivery-artifact root), as a ``/app/media/<rel>`` URL.
    """
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        return value
    try:
        resolved = Path(value).resolve()
        rel = resolved.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    parts = "/".join(quote(part) for part in rel.parts)
    return f"/app/media/{parts}"


def _media_urls(media: list[str] | None, root: Path) -> list[str]:
    if not media:
        return []
    urls: list[str] = []
    for item in media:
        url = _media_url_for(str(item), root)
        if url:
            urls.append(url)
    return urls


async def webui_media(request: web.Request) -> web.StreamResponse:
    _require_webui(request)
    if not _is_authenticated(request):
        raise web.HTTPUnauthorized()
    name = request.match_info.get("name", "")
    root = _media_root(request).resolve()
    try:
        target = (root / name).resolve()
        target.relative_to(root)  # reject path traversal outside workspace/out
    except (ValueError, OSError):
        raise web.HTTPNotFound() from None
    if not target.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(target)


# --- voice input (transcription) ------------------------------------------


def _transcription_settings(request: web.Request) -> tuple[str, str, str | None]:
    """Return (provider, api_key, language) for WebUI voice input.

    Reuses the same provider selection as the channel manager: the transcription
    provider + language come from `channels.*`, and the key from `providers.*`.
    """
    cfg = _load_current_config(request)
    channels = cfg.channels
    provider = getattr(channels, "transcription_provider", "groq") or "groq"
    language = getattr(channels, "transcription_language", None) or None
    providers = cfg.providers
    if provider == "openai":
        api_key = getattr(getattr(providers, "openai", None), "api_key", "") or ""
    else:
        api_key = getattr(getattr(providers, "groq", None), "api_key", "") or ""
    return provider, api_key, language


async def webui_transcribe(request: web.Request) -> web.Response:
    _require_webui_auth(request)
    provider, api_key, language = _transcription_settings(request)
    if not api_key:
        return web.json_response({"error": "transcription_not_configured"}, status=503)

    reader = await request.multipart()
    field = await reader.next()
    while field is not None and field.name != "audio":
        field = await reader.next()
    if field is None:
        return web.json_response({"error": "no_audio"}, status=400)

    suffix = os.path.splitext(field.filename or "")[1] or ".webm"
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                out.write(chunk)

        if provider == "openai":
            from hahobot.providers.transcription import OpenAITranscriptionProvider

            engine = OpenAITranscriptionProvider(api_key=api_key, language=language)
        else:
            from hahobot.providers.transcription import GroqTranscriptionProvider

            engine = GroqTranscriptionProvider(api_key=api_key, language=language)
        text = await engine.transcribe(tmp_path)
    except Exception as exc:  # noqa: BLE001 - report failure to the client
        logger.warning("webui transcription failed: {}", exc)
        return web.json_response({"error": "transcription_failed"}, status=502)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    return web.json_response({"text": text or ""})


# --- page + session actions -----------------------------------------------


async def webui_index(request: web.Request) -> web.Response:
    _require_webui_auth(request)
    session_key = _normalize_session_key(request.query.get("session"))
    cfg = _load_current_config(request)
    sessions = _list_webui_sessions(request)
    messages = _conversation_messages(request, session_key)
    personas = list_personas(_runtime_workspace(request))
    current_persona = _current_persona(request, session_key)
    checkpoint = _working_checkpoint(request, session_key)

    html = render_html_template(
        "gateway/webui/shell.html",
        lang=_admin_language(request),
        title=cfg.gateway.webui.title or "Hahobot",
        brand=_t(request, "webui_brand"),
        nav_chat_label=_t(request, "webui_nav_chat"),
        nav_settings_label=_t(request, "webui_nav_settings"),
        logout_label=_t(request, "admin_nav_logout"),
        language_switch_html=_markup(_language_switch(request)),
        active_session=session_key,
        sessions=sessions,
        messages=messages,
        personas=personas,
        current_persona=current_persona,
        persona_label=_t(request, "webui_persona_label"),
        checkpoint=checkpoint,
        checkpoint_heading=_t(request, "webui_checkpoint_heading"),
        checkpoint_goal_label=_t(request, "webui_checkpoint_goal"),
        checkpoint_current_label=_t(request, "webui_checkpoint_current"),
        checkpoint_next_label=_t(request, "webui_checkpoint_next"),
        mic_label=_t(request, "webui_mic_label"),
        mic_recording=_t(request, "webui_mic_recording"),
        mic_transcribing=_t(request, "webui_mic_transcribing"),
        mic_error=_t(request, "webui_mic_error"),
        can_schedule=_cron_service(request) is not None,
        schedule_label=_t(request, "webui_schedule_label"),
        schedule_delay_label=_t(request, "webui_schedule_delay"),
        schedule_message_label=_t(request, "webui_schedule_message"),
        schedule_submit=_t(request, "webui_schedule_submit"),
        default_session=_DEFAULT_WEBUI_SESSION,
        sessions_heading=_t(request, "webui_sessions_heading"),
        new_session_label=_t(request, "webui_new_session"),
        new_session_placeholder=_t(request, "webui_new_session_placeholder"),
        clear_label=_t(request, "webui_clear_session"),
        fork_label=_t(request, "webui_fork_session"),
        empty_conversation=_t(request, "webui_empty_conversation"),
        composer_placeholder=_t(request, "webui_composer_placeholder"),
        send_label=_t(request, "webui_send"),
        connecting_label=_t(request, "webui_connecting"),
        scroll_latest_label=_t(request, "webui_scroll_latest"),
        config_path=str(_current_config_path(request)),
        workspace=str(_runtime_workspace(request)),
    )
    response = web.Response(text=html, content_type="text/html")
    _set_lang_cookie(response, request)
    return response


def _token_usage(request: web.Request) -> dict[str, int]:
    agent = _agent(request)
    usage = getattr(agent, "_last_usage", None) if agent is not None else None
    if not isinstance(usage, dict):
        return {}
    out: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and value > 0:
            out[key] = value
    if "total_tokens" not in out and ("prompt_tokens" in out or "completion_tokens" in out):
        out["total_tokens"] = out.get("prompt_tokens", 0) + out.get("completion_tokens", 0)
    return out


async def webui_settings(request: web.Request) -> web.Response:
    _require_webui_auth(request)
    cfg = _load_current_config(request)
    workspace = _runtime_workspace(request)
    agent = _agent(request)

    try:
        memory = load_persona_memory_layer_status(workspace, None)
    except Exception:  # noqa: BLE001 - settings page must not hard-fail on memory read
        memory = None

    sections = [
        ("/admin", _t(request, "admin_nav_overview")),
        ("/admin/config", _t(request, "admin_nav_config")),
        ("/admin/personas", _t(request, "admin_nav_personas")),
        ("/admin/skills", _t(request, "admin_nav_skills")),
        ("/admin/cron", _t(request, "admin_nav_cron")),
        ("/admin/subagents", _t(request, "admin_nav_subagents")),
        ("/admin/commands", _t(request, "admin_nav_commands")),
        ("/admin/weixin", _t(request, "admin_nav_weixin")),
    ]

    html = render_html_template(
        "gateway/webui/settings.html",
        lang=_admin_language(request),
        title=cfg.gateway.webui.title or "Hahobot",
        nav_chat_label=_t(request, "webui_nav_chat"),
        nav_settings_label=_t(request, "webui_nav_settings"),
        logout_label=_t(request, "admin_nav_logout"),
        language_switch_html=_markup(_language_switch(request)),
        settings_heading=_t(request, "webui_settings_heading"),
        runtime_heading=_t(request, "webui_runtime_heading"),
        model_label=_t(request, "webui_model_label"),
        model=getattr(agent, "model", "") or "-",
        config_label=_t(request, "admin_meta_config"),
        config_path=str(_current_config_path(request)),
        workspace_label=_t(request, "admin_meta_workspace"),
        workspace=str(workspace),
        usage_heading=_t(request, "webui_usage_heading"),
        usage_prompt_label=_t(request, "webui_usage_prompt"),
        usage_completion_label=_t(request, "webui_usage_completion"),
        usage_total_label=_t(request, "webui_usage_total"),
        usage_empty=_t(request, "webui_usage_empty"),
        usage=_token_usage(request),
        memory_heading=_t(request, "webui_memory_heading"),
        memory=memory,
        memory_bullets_label=_t(request, "webui_memory_bullets"),
        memory_tagged_label=_t(request, "webui_memory_tagged"),
        memory_verified_label=_t(request, "webui_memory_verified"),
        sections_heading=_t(request, "webui_sections_heading"),
        sections=sections,
    )
    response = web.Response(text=html, content_type="text/html")
    _set_lang_cookie(response, request)
    return response


async def webui_session_new(request: web.Request) -> web.Response:
    _require_webui_auth(request)
    form = await request.post()
    name = str(form.get("name", "")).strip()
    ident = _SESSION_ID_RE.sub("-", name).strip("-") if name else ""
    if not ident:
        ident = secrets.token_hex(4)
    key = f"{_WEBUI_SESSION_PREFIX}{ident}"
    raise _redirect(request, f"/app?session={quote(key)}")


async def webui_session_clear(request: web.Request) -> web.Response:
    _require_webui_auth(request)
    form = await request.post()
    key = _normalize_session_key(str(form.get("session", "")))
    sm = _session_manager(request)
    if sm is not None:
        session = sm.get_or_create(key)
        session.clear()
        sm.save(session)
    raise _redirect(request, f"/app?session={quote(key)}")


async def webui_session_fork(request: web.Request) -> web.Response:
    """Branch the current conversation into a new webui session (copy on write)."""
    _require_webui_auth(request)
    form = await request.post()
    source_key = _normalize_session_key(str(form.get("session", "")))
    sm = _session_manager(request)
    if sm is None:
        raise _redirect(request, f"/app?session={quote(source_key)}")

    source = sm.get_or_create(source_key)
    base_ident = source_key.split(":", 1)[1]
    fork_key = f"{_WEBUI_SESSION_PREFIX}{base_ident}-fork-{secrets.token_hex(3)}"
    fork = sm.get_or_create(fork_key)
    fork.messages = copy.deepcopy(source.messages)
    fork.metadata = copy.deepcopy(source.metadata)
    fork.last_consolidated = source.last_consolidated
    fork._requires_full_save = True
    sm.save(fork)
    raise _redirect(request, f"/app?session={quote(fork_key)}")


async def webui_schedule(request: web.Request) -> web.Response:
    """Schedule a one-off reminder that pushes into the current webui session.

    Creates a cron job with ``channel="webui", to=<id>`` so the existing cron
    ``on_job`` path delivers it back through the WebUIChannel (live push) and
    persists it into the ``webui:<id>`` session (offline fallback).
    """
    _require_webui_auth(request)
    cron = _cron_service(request)
    form = await request.post()
    key = _normalize_session_key(str(form.get("session", "")))
    chat_id = key.split(":", 1)[1]
    message = str(form.get("message", "")).strip()
    try:
        delay_min = float(str(form.get("delay", "")).strip() or "0")
    except ValueError:
        delay_min = 0.0

    if cron is not None and message and delay_min > 0:
        from hahobot.cron.types import CronSchedule

        at_ms = int(time.time() * 1000) + int(delay_min * 60_000)
        cron.add_job(
            name="webui-reminder",
            schedule=CronSchedule(kind="at", at_ms=at_ms),
            message=message,
            deliver=True,
            channel="webui",
            to=chat_id,
            delete_after_run=True,
        )
    raise _redirect(request, f"/app?session={quote(key)}")


# --- chat WebSocket -------------------------------------------------------


async def webui_chat_ws(request: web.Request) -> web.WebSocketResponse:
    _require_webui(request)
    if not _is_authenticated(request):
        raise web.HTTPUnauthorized()
    agent = _agent(request)
    if agent is None:
        raise web.HTTPServiceUnavailable(text="agent unavailable")

    # The connection is bound to one conversation for its lifetime: the session
    # selected on the page. This is the key proactive pushes (cron/heartbeat) are
    # broadcast under, so it must match the page's `webui:<id>`.
    session_key = _normalize_session_key(request.query.get("session"))
    chat_id = session_key.split(":", 1)[1]

    ws = web.WebSocketResponse(heartbeat=25.0, max_msg_size=_MAX_WS_MSG_BYTES)
    await ws.prepare(request)

    broadcaster = _broadcaster(request)
    conn = WebUIConnection(session_key) if broadcaster is not None else None

    async def _emit(frame: dict[str, Any]) -> None:
        """Single-writer sink: every outbound frame goes through the per-conn queue."""
        if conn is not None:
            conn.enqueue(frame)
        else:
            await ws.send_json(frame)

    async def _writer() -> None:
        if conn is None:
            return
        while True:
            frame = await conn.queue.get()
            if frame is None:  # sentinel → shut down
                return
            try:
                await ws.send_json(frame)
            except (ConnectionResetError, RuntimeError):
                return

    writer_task = asyncio.create_task(_writer()) if conn is not None else None
    if broadcaster is not None and conn is not None:
        broadcaster.register(conn)

    await _emit({"event": "ready"})

    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                logger.warning("webui ws connection error: {}", ws.exception())
                continue
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("event") != "message":
                continue
            text = str(data.get("text") or data.get("content") or "").strip()
            if not text:
                continue

            async def on_stream(delta: str) -> None:
                if delta:
                    await _emit({"event": "delta", "text": delta})

            async def on_progress(hint: str, tool_hint: bool = False) -> None:
                if hint:
                    await _emit({"event": "progress", "text": hint, "tool_hint": tool_hint})

            try:
                resp = await agent.process_direct(
                    text,
                    session_key=session_key,
                    channel="webui",
                    chat_id=chat_id,
                    on_progress=on_progress,
                    on_stream=on_stream,
                )
            except Exception as exc:  # noqa: BLE001 - report failures to the client
                logger.exception("webui chat turn failed")
                await _emit({"event": "error", "text": str(exc)})
                continue

            media = _media_urls(
                getattr(resp, "media", None) if resp else None, _media_root(request)
            )
            await _emit(
                {
                    "event": "stream_end",
                    "text": resp.content if resp else "",
                    "media": media,
                    "checkpoint": _working_checkpoint(request, session_key),
                }
            )
    finally:
        if broadcaster is not None and conn is not None:
            broadcaster.unregister(conn)
        if conn is not None:
            conn.close()
        if writer_task is not None:
            try:
                await writer_task
            except asyncio.CancelledError:
                pass

    return ws


def register_webui_routes(
    app: web.Application,
    *,
    agent: object | None = None,
    session_manager: object | None = None,
    broadcaster: WebUIBroadcaster | None = None,
    cron_service: object | None = None,
) -> None:
    """Register the WebUI routes on the gateway aiohttp app."""
    if agent is not None:
        app[_WEBUI_AGENT_KEY] = agent
    if session_manager is not None:
        app[_WEBUI_SESSION_MANAGER_KEY] = session_manager
    if broadcaster is not None:
        app[_WEBUI_BROADCASTER_KEY] = broadcaster
    if cron_service is not None:
        app[_WEBUI_CRON_KEY] = cron_service
    app.router.add_get("/app", webui_index)
    app.router.add_get("/app/settings", webui_settings)
    app.router.add_get("/app/media/{name:.*}", webui_media)
    app.router.add_post("/app/transcribe", webui_transcribe)
    app.router.add_get("/app/ws", webui_chat_ws)
    app.router.add_post("/app/session/new", webui_session_new)
    app.router.add_post("/app/session/clear", webui_session_clear)
    app.router.add_post("/app/session/fork", webui_session_fork)
    app.router.add_post("/app/schedule", webui_schedule)
