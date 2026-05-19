"""Weixin QR-login flow for the admin UI."""

from __future__ import annotations

import base64
import io
import json
import secrets
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from aiohttp import web

from hahobot.gateway.admin.base import (
    _current_config_path,
    _load_current_config,
    _markup,
    _page,
    _redirect,
    _require_admin_auth,
    _t,
    _th,
)
from hahobot.gateway.admin.constants import (
    _ADMIN_WEIXIN_LOGIN_SESSIONS_KEY,
    _WEIXIN_ADMIN_SESSION_TTL_S,
)


@dataclass
class WeixinAdminLoginSession:
    """Ephemeral Weixin QR-login state stored by the admin UI."""

    session_id: str
    qrcode_id: str
    scan_url: str
    qr_image_data_url: str | None
    poll_base_url: str
    started_at: float
    updated_at: float
    status: str = "pending"
    refresh_count: int = 0
    bot_id: str = ""
    user_id: str = ""
    error: str = ""


def _weixin_login_sessions(request: web.Request) -> dict[str, WeixinAdminLoginSession]:
    raw = request.app[_ADMIN_WEIXIN_LOGIN_SESSIONS_KEY]
    if isinstance(raw, dict):
        return raw
    sessions: dict[str, WeixinAdminLoginSession] = {}
    request.app[_ADMIN_WEIXIN_LOGIN_SESSIONS_KEY] = sessions
    return sessions


def _prune_weixin_login_sessions(request: web.Request) -> None:
    cutoff = time.time() - _WEIXIN_ADMIN_SESSION_TTL_S
    sessions = _weixin_login_sessions(request)
    for session_id in list(sessions):
        session = sessions[session_id]
        if session.updated_at < cutoff:
            sessions.pop(session_id, None)


def _weixin_qr_image_data_url(url: str) -> str | None:
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ImportError:
        return None

    qr = qrcode.QRCode(border=1, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    output = io.BytesIO()
    image.save(output)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _weixin_state_file_path(request: web.Request, channel_config: Any) -> Path:
    state_root = (
        Path(channel_config.state_dir).expanduser()
        if getattr(channel_config, "state_dir", "")
        else _current_config_path(request).parent / "weixin"
    )
    return state_root / "account.json"


def _weixin_saved_state_snapshot(request: web.Request, channel_config: Any) -> dict[str, Any]:
    state_file = _weixin_state_file_path(request, channel_config)
    snapshot = {
        "state_file": state_file,
        "token_present": False,
        "base_url": "",
        "context_tokens": 0,
        "cursor_present": False,
    }
    if not state_file.exists():
        return snapshot
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return snapshot
    context_tokens = data.get("context_tokens")
    snapshot["token_present"] = bool(str(data.get("token", "")).strip())
    snapshot["base_url"] = str(data.get("base_url", "") or "").strip()
    snapshot["context_tokens"] = len(context_tokens) if isinstance(context_tokens, dict) else 0
    snapshot["cursor_present"] = bool(str(data.get("get_updates_buf", "") or "").strip())
    return snapshot


def _clear_weixin_saved_state(request: web.Request, channel_config: Any) -> None:
    state_file = _weixin_state_file_path(request, channel_config)
    if state_file.exists():
        state_file.unlink()


async def _start_weixin_login_session(
    request: web.Request,
    *,
    force: bool,
) -> WeixinAdminLoginSession:
    from hahobot.channels.weixin import WeixinChannel

    config = _load_current_config(request)
    channel_config = config.channels.weixin
    if force:
        _clear_weixin_saved_state(request, channel_config)

    channel = WeixinChannel(channel_config, None)  # type: ignore[arg-type]
    channel._client = httpx.AsyncClient(
        timeout=httpx.Timeout(60, connect=30),
        follow_redirects=True,
    )
    try:
        qrcode_id, scan_url = await channel._fetch_qr_code()
    finally:
        await channel._client.aclose()
        channel._client = None

    now = time.time()
    return WeixinAdminLoginSession(
        session_id=secrets.token_urlsafe(12),
        qrcode_id=qrcode_id,
        scan_url=scan_url,
        qr_image_data_url=_weixin_qr_image_data_url(scan_url),
        poll_base_url=channel_config.base_url,
        started_at=now,
        updated_at=now,
    )


async def _advance_weixin_login_session(
    request: web.Request,
    session: WeixinAdminLoginSession,
) -> WeixinAdminLoginSession:
    from hahobot.channels.weixin import MAX_QR_REFRESH_COUNT, WeixinChannel

    if session.status in {"confirmed", "error"}:
        return session

    config = _load_current_config(request)
    channel_config = config.channels.weixin
    channel = WeixinChannel(channel_config, None)  # type: ignore[arg-type]
    if not channel.config.state_dir:
        channel.config.state_dir = str(_current_config_path(request).parent / "weixin")
    channel._client = httpx.AsyncClient(
        timeout=httpx.Timeout(60, connect=30),
        follow_redirects=True,
    )
    try:
        try:
            status_data = await channel._api_get_with_base(
                base_url=session.poll_base_url,
                endpoint="ilink/bot/get_qrcode_status",
                params={"qrcode": session.qrcode_id},
                auth=False,
            )
        except Exception as exc:
            if channel._is_retryable_qr_poll_error(exc):
                session.updated_at = time.time()
                return session
            session.status = "error"
            session.error = str(exc)
            session.updated_at = time.time()
            return session

        if not isinstance(status_data, dict):
            session.updated_at = time.time()
            return session

        status = str(status_data.get("status", "") or "").strip()
        session.updated_at = time.time()
        if status == "confirmed":
            token = str(status_data.get("bot_token", "") or "").strip()
            if not token:
                session.status = "error"
                session.error = _t(request, "admin_weixin_error_missing_token")
                return session
            base_url = str(status_data.get("baseurl", "") or "").strip()
            if base_url:
                channel.config.base_url = base_url
            channel._token = token
            channel._save_state()
            session.status = "confirmed"
            session.bot_id = str(status_data.get("ilink_bot_id", "") or "").strip()
            session.user_id = str(status_data.get("ilink_user_id", "") or "").strip()
            session.error = ""
            return session

        if status == "scaned_but_redirect":
            redirect_host = str(status_data.get("redirect_host", "") or "").strip()
            if redirect_host:
                session.poll_base_url = (
                    redirect_host
                    if redirect_host.startswith(("http://", "https://"))
                    else f"https://{redirect_host}"
                )
            return session

        if status == "expired":
            session.refresh_count += 1
            if session.refresh_count > MAX_QR_REFRESH_COUNT:
                session.status = "error"
                session.error = _t(request, "admin_weixin_error_expired_too_many")
                return session
            qrcode_id, scan_url = await channel._fetch_qr_code()
            session.qrcode_id = qrcode_id
            session.scan_url = scan_url
            session.qr_image_data_url = _weixin_qr_image_data_url(scan_url)
            session.poll_base_url = channel.config.base_url
            return session

        return session
    finally:
        await channel._client.aclose()
        channel._client = None


def _render_weixin_page(
    request: web.Request,
    *,
    session: WeixinAdminLoginSession | None,
    flash: str | None = None,
    error: str | None = None,
) -> web.Response:
    config = _load_current_config(request)
    channel_config = config.channels.weixin
    saved_state = _weixin_saved_state_snapshot(request, channel_config)
    config_token_present = bool(channel_config.token.strip())
    state_notice = (
        _th(request, "admin_weixin_config_token_notice")
        if config_token_present
        else _th(request, "admin_weixin_state_file_notice")
    )

    session_card = f"""
      <section class="card stack">
        <div class="section-head">
          <h2>{escape(_t(request, "admin_weixin_qr_title"))}</h2>
          <div class="muted">{_th(request, "admin_weixin_qr_desc")}</div>
        </div>
        <div class="notice">{state_notice}</div>
      </section>
    """
    pending_session_id = ""

    if session is not None:
        status_map = {
            "pending": ("pill hot", _t(request, "admin_weixin_status_pending")),
            "confirmed": ("pill hot", _t(request, "admin_weixin_status_confirmed")),
            "error": ("pill restart", _t(request, "admin_weixin_status_error")),
        }
        status_class, status_label = status_map.get(
            session.status,
            ("pill", escape(session.status)),
        )
        qr_preview = (
            f'<div class="qr-preview"><img src="{escape(session.qr_image_data_url)}" alt="{escape(_t(request, "admin_weixin_qr_alt"))}"></div>'
            if session.qr_image_data_url
            else f'<div class="notice">{_th(request, "admin_weixin_qr_no_image")}</div>'
        )
        details = [
            f'<div class="weixin-status"><span class="{status_class}">{escape(status_label)}</span></div>',
            f'<div class="muted">{escape(_t(request, "admin_weixin_label_session"))}: <code>{escape(session.session_id)}</code></div>',
            f'<div class="muted">{escape(_t(request, "admin_weixin_label_poll_base"))}: <code>{escape(session.poll_base_url)}</code></div>',
            f'<div class="muted">{escape(_t(request, "admin_weixin_label_refresh_count"))}: <code>{session.refresh_count}</code></div>',
        ]
        if session.bot_id:
            details.append(
                f'<div class="muted">{escape(_t(request, "admin_weixin_label_bot_id"))}: <code>{escape(session.bot_id)}</code></div>'
            )
        if session.user_id:
            details.append(
                f'<div class="muted">{escape(_t(request, "admin_weixin_label_user_id"))}: <code>{escape(session.user_id)}</code></div>'
            )
        if session.error:
            details.append(f'<div class="notice error">{escape(session.error)}</div>')
        actions = ""
        if session.status == "pending":
            actions = f"""
              <div class="inline-actions">
                <form method="post" action="/admin/weixin/cancel" class="inline-form">
                  <input type="hidden" name="session" value="{escape(session.session_id)}">
                  <button type="submit" class="ghost">{escape(_t(request, "admin_weixin_cancel"))}</button>
                </form>
              </div>
            """
            pending_session_id = session.session_id
        session_card = f"""
          <section class="card stack">
            <div class="section-head">
              <h2>{escape(_t(request, "admin_weixin_qr_title"))}</h2>
              <div class="muted">{_th(request, "admin_weixin_qr_desc")}</div>
            </div>
            <div class="qr-shell">
              {qr_preview}
              <div class="stack">
                {"".join(details)}
                <pre class="code-block"><code>{escape(session.scan_url)}</code></pre>
                <div class="muted">{_th(request, "admin_weixin_scan_hint")}</div>
                {actions}
              </div>
            </div>
          </section>
        """

    return _page(
        template_name="gateway/admin/weixin.html",
        title=_t(request, "admin_weixin_title"),
        heading=_t(request, "admin_weixin_heading"),
        request=request,
        flash=flash,
        error=error,
        weixin_nav_label=_t(request, "admin_nav_weixin"),
        weixin_intro_html=_markup(_th(request, "admin_weixin_intro")),
        start_label=_t(request, "admin_weixin_start"),
        force_start_label=_t(request, "admin_weixin_force_start"),
        saved_state_title=_t(request, "admin_weixin_saved_state_title"),
        saved_state_desc_html=_markup(_th(request, "admin_weixin_saved_state_desc")),
        state_file_label=_t(request, "admin_weixin_label_state_file"),
        state_file_path=str(saved_state["state_file"]),
        saved_token_label=_t(request, "admin_weixin_label_saved_token"),
        saved_token_text=_t(
            request,
            "admin_boolean_true" if saved_state["token_present"] else "admin_boolean_false",
        ),
        config_token_label=_t(request, "admin_weixin_label_config_token"),
        config_token_text=_t(
            request,
            "admin_boolean_true" if config_token_present else "admin_boolean_false",
        ),
        context_tokens_label=_t(request, "admin_weixin_label_context_tokens"),
        context_tokens=saved_state["context_tokens"],
        session_card_html=_markup(session_card),
        pending_session_id=pending_session_id,
    )


async def _admin_weixin_page(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    _prune_weixin_login_sessions(request)
    sessions = _weixin_login_sessions(request)
    session = None
    flash = None
    error = None
    session_id = str(request.query.get("session", "") or "").strip()
    if request.query.get("cancelled") == "1":
        flash = _t(request, "admin_weixin_cancelled_flash")
    if session_id:
        session = sessions.get(session_id)
        if session is None:
            error = _t(request, "admin_weixin_missing_session")
        else:
            session = await _advance_weixin_login_session(request, session)
            sessions[session_id] = session
            if session.status == "confirmed":
                flash = _t(request, "admin_weixin_confirmed_flash")
            elif session.status == "error" and session.error:
                error = _t(request, "admin_weixin_status_error_detail", error=session.error)
    return _render_weixin_page(request, session=session, flash=flash, error=error)


async def _admin_weixin_start(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    _prune_weixin_login_sessions(request)
    form = await request.post()
    force = str(form.get("force", "")).lower() in {"1", "true", "on", "yes"}
    try:
        session = await _start_weixin_login_session(request, force=force)
    except Exception as exc:
        return _render_weixin_page(
            request,
            session=None,
            error=_t(request, "admin_weixin_start_failed", error=exc),
        )
    sessions = _weixin_login_sessions(request)
    sessions[session.session_id] = session
    raise _redirect(request, f"/admin/weixin?session={quote(session.session_id, safe='')}")


async def _admin_weixin_cancel(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    form = await request.post()
    session_id = str(form.get("session", "") or "").strip()
    if session_id:
        _weixin_login_sessions(request).pop(session_id, None)
    raise _redirect(request, "/admin/weixin?cancelled=1")
