"""Foundational helpers for the admin UI: config IO, auth, i18n and page chrome."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from aiohttp import web
from markupsafe import Markup

from hahobot.agent.i18n import language_label, normalize_language_code
from hahobot.agent.i18n import text as i18n_text
from hahobot.config.loader import _migrate_config, load_config
from hahobot.config.schema import Config
from hahobot.gateway.admin.constants import (
    _ADMIN_CONFIG_PATH_KEY,
    _ADMIN_COOKIE,
    _ADMIN_COOKIE_TTL_S,
    _ADMIN_LANG_COOKIE,
    _ADMIN_LANG_COOKIE_TTL_S,
    _ADMIN_WORKSPACE_KEY,
    _DEFAULT_ADMIN_LANG,
    _LEGACY_ADMIN_COOKIE,
    _LEGACY_ADMIN_LANG_COOKIE,
)
from hahobot.utils.html_templates import render_html_template


def _current_config_path(request: web.Request) -> Path:
    return Path(request.app[_ADMIN_CONFIG_PATH_KEY])


def _runtime_workspace(request: web.Request) -> Path:
    return Path(request.app[_ADMIN_WORKSPACE_KEY])


def _load_current_config(request: web.Request) -> Config:
    return load_config(_current_config_path(request))


def _load_raw_config_data(request: web.Request) -> dict[str, Any]:
    path = _current_config_path(request)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json must contain a JSON object.")
    return _migrate_config(data)


def _save_raw_config_data(request: web.Request, data: dict[str, Any]) -> None:
    path = _current_config_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to a temp file then rename, so a crash mid-write
    # cannot leave a truncated/corrupt config.json.
    import tempfile

    content = _pretty_json(data) + "\n"
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp_path).replace(path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _admin_enabled(request: web.Request) -> bool:
    try:
        return bool(_load_current_config(request).gateway.admin.enabled)
    except Exception:
        return False


def _admin_auth_key(request: web.Request) -> str:
    try:
        return (_load_current_config(request).gateway.admin.auth_key or "").strip()
    except Exception:
        return ""


def _require_admin_enabled(request: web.Request) -> None:
    if not _admin_enabled(request):
        raise web.HTTPNotFound()


def _session_signature(auth_key: str, expires_at: int, nonce: str) -> str:
    payload = f"{expires_at}:{nonce}".encode()
    return hmac.new(auth_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _build_session_cookie(auth_key: str) -> str:
    expires_at = int(time.time()) + _ADMIN_COOKIE_TTL_S
    nonce = secrets.token_hex(12)
    signature = _session_signature(auth_key, expires_at, nonce)
    return f"{expires_at}:{nonce}:{signature}"


def _is_authenticated(request: web.Request) -> bool:
    auth_key = _admin_auth_key(request)
    if not auth_key:
        return False

    raw = request.cookies.get(_ADMIN_COOKIE) or request.cookies.get(_LEGACY_ADMIN_COOKIE, "")
    parts = raw.split(":", 2)
    if len(parts) != 3:
        return False

    expires_at_raw, nonce, signature = parts
    try:
        expires_at = int(expires_at_raw)
    except ValueError:
        return False
    if expires_at < int(time.time()):
        return False

    expected = _session_signature(auth_key, expires_at, nonce)
    return hmac.compare_digest(signature, expected)


def _normalize_next_path(value: str | None) -> str:
    if not isinstance(value, str):
        return "/admin"
    value = value.strip()
    if not value.startswith("/admin"):
        return "/admin"
    return value


def _admin_language(request: web.Request) -> str:
    query_lang = normalize_language_code(request.query.get("lang"))
    if query_lang:
        return query_lang
    cookie_lang = normalize_language_code(
        request.cookies.get(_ADMIN_LANG_COOKIE) or request.cookies.get(_LEGACY_ADMIN_LANG_COOKIE)
    )
    if cookie_lang:
        return cookie_lang
    return _DEFAULT_ADMIN_LANG


def _t(request: web.Request, key: str, **kwargs: Any) -> str:
    return i18n_text(_admin_language(request), key, **kwargs)


def _th(request: web.Request, key: str, **kwargs: Any) -> str:
    safe_kwargs = {name: escape(str(value)) for name, value in kwargs.items()}
    return _t(request, key, **safe_kwargs)


def _language_switch_label(code: str, ui_language: str) -> str:
    label = language_label(code, ui_language)
    if "(" in label and label.endswith(")"):
        return label.split("(", 1)[1][:-1]
    return label


def _set_lang_cookie(response: web.StreamResponse, request: web.Request) -> web.StreamResponse:
    for cookie_name in (_ADMIN_LANG_COOKIE, _LEGACY_ADMIN_LANG_COOKIE):
        response.set_cookie(
            cookie_name,
            _admin_language(request),
            max_age=_ADMIN_LANG_COOKIE_TTL_S,
            samesite="Lax",
        )
    return response


def _redirect(request: web.Request, location: str) -> web.HTTPFound:
    response = web.HTTPFound(location)
    _set_lang_cookie(response, request)
    return response


def _require_admin_auth(request: web.Request) -> None:
    _require_admin_enabled(request)
    if _is_authenticated(request):
        return
    destination = quote(str(request.rel_url), safe="/?=&")
    raise _redirect(request, f"/admin/login?next={destination}")


def _query_url(request: web.Request, **updates: str | None) -> str:
    params = dict(request.query)
    for key, value in updates.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    query = urlencode(params)
    return f"{request.path}?{query}" if query else request.path


def _language_switch(request: web.Request) -> str:
    active = _admin_language(request)
    links: list[str] = []
    for code in ("zh", "en"):
        href = escape(_query_url(request, lang=code))
        label = escape(_language_switch_label(code, active))
        css_class = "lang-link active" if code == active else "lang-link"
        links.append(f'<a class="{css_class}" href="{href}">{label}</a>')
    return (
        f'<div class="lang-switch"><span class="muted">{escape(_t(request, "admin_meta_language"))}</span>'
        f"{''.join(links)}</div>"
    )


def _nav_link(request: web.Request, href: str, label_key: str) -> str:
    path = request.path
    if href == "/admin":
        active = path == href
    else:
        active = path == href or path.startswith(f"{href}/")
    css_class = "nav-link active" if active else "nav-link"
    return f'<a class="{css_class}" href="{href}">{escape(_t(request, label_key))}</a>'


def _markup(value: str | Markup | None = None) -> Markup:
    if value is None:
        return Markup("")
    if isinstance(value, Markup):
        return value
    return Markup(value)


def _page(
    *,
    template_name: str,
    title: str,
    request: web.Request,
    heading: str | None = None,
    flash: str | None = None,
    error: str | None = None,
    **context: Any,
) -> web.Response:
    heading_text = heading or title
    lang = _admin_language(request)
    nav = ""
    if _is_authenticated(request):
        nav = (
            '<nav class="nav">'
            f"{_nav_link(request, '/admin', 'admin_nav_overview')}"
            f"{_nav_link(request, '/admin/sessions', 'admin_nav_sessions')}"
            f"{_nav_link(request, '/admin/skills', 'admin_nav_skills')}"
            f"{_nav_link(request, '/admin/subagents', 'admin_nav_subagents')}"
            f"{_nav_link(request, '/admin/cron', 'admin_nav_cron')}"
            f"{_nav_link(request, '/admin/config', 'admin_nav_config')}"
            f"{_nav_link(request, '/admin/weixin', 'admin_nav_weixin')}"
            f"{_nav_link(request, '/admin/commands', 'admin_nav_commands')}"
            f"{_nav_link(request, '/admin/personas', 'admin_nav_personas')}"
            '<form method="post" action="/admin/logout" class="inline-form">'
            f'<button type="submit" class="ghost nav-link nav-link-button">{escape(_t(request, "admin_nav_logout"))}</button>'
            "</form>"
            "</nav>"
        )

    notices: list[str] = []
    if flash:
        notices.append(f'<div class="notice success">{escape(flash)}</div>')
    if error:
        notices.append(f'<div class="notice error">{escape(error)}</div>')

    html = render_html_template(
        template_name,
        title=title,
        brand=_t(request, "admin_brand"),
        heading_text=heading_text,
        lang=lang,
        config_path=str(_current_config_path(request)),
        workspace=str(_runtime_workspace(request)),
        admin_meta_config_label=_t(request, "admin_meta_config"),
        admin_meta_workspace_label=_t(request, "admin_meta_workspace"),
        language_switch_html=_markup(_language_switch(request)),
        nav_html=_markup(nav),
        notices_html=_markup("".join(notices)),
        **context,
    )
    response = web.Response(text=html, content_type="text/html")
    _set_lang_cookie(response, request)
    return response


def _pretty_json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _read_json_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return _pretty_json(json.loads(path.read_text(encoding="utf-8")))
    except ValueError:
        return path.read_text(encoding="utf-8")


def _parse_iso_datetime(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _format_iso_datetime(value: str | None) -> str:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return value or "-"
    try:
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _format_epoch_ms(value: int | None) -> str:
    if value is None or value <= 0:
        return "-"
    return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _format_duration_ms(value: int | None) -> str:
    if value is None or value <= 0:
        return "-"
    total_seconds = max(value // 1000, 1)
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _render_login_page(
    request: web.Request,
    *,
    next_path: str,
    missing_key_body_html: str | None = None,
    form_only: bool = False,
    error: str | None = None,
) -> web.Response:
    feature_items_html = _markup(
        "".join(
            f"<li>{_th(request, key)}</li>"
            for key in (
                "admin_card_config_desc",
                "admin_card_commands_desc",
                "admin_card_personas_desc",
            )
        )
    )
    return _page(
        template_name="gateway/admin/login.html",
        title=_t(request, "admin_login_title"),
        heading=_t(request, "admin_login_heading"),
        request=request,
        error=error,
        missing_key=missing_key_body_html is not None,
        missing_key_body_html=_markup(missing_key_body_html),
        form_only=form_only,
        next_path=next_path,
        auth_key_label=_t(request, "admin_login_key_label"),
        submit_label=_t(request, "admin_login_submit"),
        login_feature_items_html=feature_items_html,
    )


async def _admin_login_page(request: web.Request) -> web.Response:
    _require_admin_enabled(request)
    if _is_authenticated(request):
        raise _redirect(request, _normalize_next_path(request.query.get("next")))

    auth_key = _admin_auth_key(request)
    if not auth_key:
        return _render_login_page(
            request,
            next_path="",
            missing_key_body_html=_th(request, "admin_login_missing_key_body"),
            error=_t(request, "admin_login_missing_key_error"),
        )

    next_path = _normalize_next_path(request.query.get("next"))
    return _render_login_page(request, next_path=next_path)


async def _admin_login_submit(request: web.Request) -> web.Response:
    _require_admin_enabled(request)
    form = await request.post()
    auth_key = _admin_auth_key(request)
    next_path = _normalize_next_path(form.get("next"))

    if not auth_key:
        return _render_login_page(
            request,
            next_path="",
            missing_key_body_html=_th(request, "admin_login_configure_key"),
            error=_t(request, "admin_login_missing_key_error"),
        )

    submitted = str(form.get("auth_key", ""))
    if not hmac.compare_digest(submitted, auth_key):
        return _render_login_page(
            request,
            next_path=next_path,
            form_only=True,
            error=_t(request, "admin_login_invalid_error"),
        )

    response = _redirect(request, next_path)
    cookie_value = _build_session_cookie(auth_key)
    for cookie_name in (_ADMIN_COOKIE, _LEGACY_ADMIN_COOKIE):
        response.set_cookie(
            cookie_name,
            cookie_value,
            max_age=_ADMIN_COOKIE_TTL_S,
            httponly=True,
            samesite="Strict",
        )
    raise response


async def _admin_logout(request: web.Request) -> web.Response:
    _require_admin_enabled(request)
    response = _redirect(request, "/admin/login")
    response.del_cookie(_ADMIN_COOKIE)
    response.del_cookie(_LEGACY_ADMIN_COOKIE)
    raise response
