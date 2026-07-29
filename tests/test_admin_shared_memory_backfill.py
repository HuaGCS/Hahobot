"""Focused coverage for the Admin shared-Mem0 backfill workflow."""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from multidict import MultiDict, MultiDictProxy

from hahobot.config.loader import save_config
from hahobot.config.schema import Config, SharedMemoryConfig
from hahobot.gateway.admin import memory as memory_admin
from hahobot.gateway.http import create_http_app


async def _call_route(
    app: web.Application,
    method: str,
    path: str,
    *,
    cookies: dict[str, str] | None = None,
    data: dict[str, str] | list[tuple[str, str]] | None = None,
) -> web.StreamResponse:
    headers: dict[str, str] = {}
    if cookies:
        headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
    request = make_mocked_request(method, path, headers=headers, app=app)
    if data is not None:
        form = MultiDictProxy(MultiDict(data))

        async def _post() -> MultiDictProxy[str]:
            return form

        request.post = _post  # type: ignore[method-assign]
    match = await app.router.resolve(request)
    match.add_app(app)
    request._match_info = match  # type: ignore[attr-defined]
    try:
        return await match.handler(request)
    except web.HTTPException as exc:
        return exc


def _configured_app(tmp_path: Path) -> tuple[web.Application, Path, Path]:
    config_path = tmp_path / "instance" / "config.json"
    workspace = tmp_path / "runtime-workspace"
    workspace.mkdir(parents=True)
    config = Config()
    config.gateway.admin.enabled = True
    config.gateway.admin.auth_key = "admin-secret"
    # This deliberately differs from the app-injected runtime workspace. The
    # Admin operation must never retarget itself from config at request time.
    config.agents.defaults.workspace = str(tmp_path / "configured-but-not-running")
    config.memory.shared = SharedMemoryConfig.model_validate(
        {
            "enabled": True,
            "provider": "mem0",
            "baseUrl": "https://mem0.example.test",
            "apiKey": "mem0-api-key-needle",
            "userId": "shared-user",
            "personaEnabled": True,
            "personaUserIdPrefix": "shared-private",
            "globalWriteMode": "full",
            "writeEnabled": True,
        }
    )
    save_config(config, config_path)
    return create_http_app(config_path=config_path, workspace=workspace), config_path, workspace


async def _login(app: web.Application) -> dict[str, str]:
    response = await _call_route(
        app,
        "POST",
        "/admin/login",
        data={"auth_key": "admin-secret", "next": "/admin/memory/shared"},
    )
    assert response.status == 302
    return {"hahobot_admin_session": response.cookies["hahobot_admin_session"].value}


def _form_value(page: str, action: str, name: str) -> str:
    form_match = re.search(
        rf'<form[^>]+action="{re.escape(action)}".*?</form>',
        page,
        flags=re.DOTALL,
    )
    assert form_match is not None, page
    value_match = re.search(
        rf'<input[^>]+name="{re.escape(name)}"[^>]+value="([^"]*)"',
        form_match.group(0),
    )
    assert value_match is not None, form_match.group(0)
    return html.unescape(value_match.group(1))


@pytest.mark.asyncio
async def test_shared_memory_admin_requires_auth_and_hides_action_until_ready(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config = Config()
    config.gateway.admin.enabled = True
    config.gateway.admin.auth_key = "admin-secret"
    save_config(config, config_path)
    app = create_http_app(config_path=config_path, workspace=workspace)

    denied = await _call_route(app, "GET", "/admin/memory/shared")
    assert denied.status == 302
    assert denied.headers["Location"].startswith("/admin/login?next=")

    cookies = await _login(app)
    page = await _call_route(app, "GET", "/admin/memory/shared", cookies=cookies)
    assert page.status == 200
    assert 'action="/admin/memory/shared/preview"' not in page.text

    config_page = await _call_route(app, "GET", "/admin/config", cookies=cookies)
    assert config_page.status == 200
    assert 'href="/admin/memory/shared"' not in config_page.text

    rejected = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/preview",
        cookies=cookies,
        data={"form_token": "invalid", "persona": ""},
    )
    assert rejected.status == 403


@pytest.mark.asyncio
async def test_shared_memory_admin_preview_is_read_only_and_content_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_path, workspace = _configured_app(tmp_path)
    (workspace / "PROFILE.md").write_text(
        "public-memory-body-needle\n\n<private>private-memory-body-needle</private>",
        encoding="utf-8",
    )
    cookies = await _login(app)

    async def forbidden_execute(*args, **kwargs):
        raise AssertionError("preview must not execute or create shared-memory state")

    monkeypatch.setattr(memory_admin, "execute_shared_memory_backfill", forbidden_execute)
    page = await _call_route(app, "GET", "/admin/memory/shared", cookies=cookies)
    assert page.status == 200
    assert str(workspace) in page.text
    assert str(tmp_path / "configured-but-not-running") not in page.text
    assert 'action="/admin/memory/shared/preview"' in page.text
    config_page = await _call_route(app, "GET", "/admin/config", cookies=cookies)
    assert config_page.status == 200
    assert 'href="/admin/memory/shared"' in config_page.text
    preview_token = _form_value(page.text, "/admin/memory/shared/preview", "form_token")

    preview = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/preview",
        cookies=cookies,
        data={"form_token": preview_token, "persona": ""},
    )
    assert preview.status == 200
    assert 'action="/admin/memory/shared/apply"' in preview.text
    assert "shared-user" in preview.text
    assert "public-memory-body-needle" not in preview.text
    assert "private-memory-body-needle" not in preview.text
    assert "mem0-api-key-needle" not in preview.text
    assert not (config_path.parent / "shared-memory").exists()
    assert list(tmp_path.rglob("shared.sqlite")) == []


@pytest.mark.asyncio
async def test_shared_memory_admin_apply_rebuilds_plan_and_uses_instance_state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, config_path, workspace = _configured_app(tmp_path)
    (workspace / "PROFILE.md").write_text("stable public preference", encoding="utf-8")
    cookies = await _login(app)
    page = await _call_route(app, "GET", "/admin/memory/shared", cookies=cookies)
    preview_token = _form_value(page.text, "/admin/memory/shared/preview", "form_token")
    preview = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/preview",
        cookies=cookies,
        data={"form_token": preview_token, "persona": ""},
    )
    apply_token = _form_value(preview.text, "/admin/memory/shared/apply", "form_token")
    fingerprint = _form_value(
        preview.text,
        "/admin/memory/shared/apply",
        "plan_fingerprint",
    )
    captured: dict[str, object] = {}

    async def fake_execute(plan, config, **kwargs):
        captured["workspace"] = plan.workspace
        captured["state_root"] = kwargs["state_root"]
        captured["kwargs"] = kwargs
        return "complete", {item.event_id: "delivered" for item in plan.items}

    monkeypatch.setattr(memory_admin, "execute_shared_memory_backfill", fake_execute)
    applied = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/apply",
        cookies=cookies,
        data={
            "form_token": apply_token,
            "persona": "",
            "plan_fingerprint": fingerprint,
        },
    )

    assert applied.status == 200
    assert captured["workspace"] == workspace.resolve()
    assert captured["state_root"] == config_path.resolve().parent / "shared-memory"
    assert "force" not in captured["kwargs"]
    assert "stable public preference" not in applied.text
    assert "mem0-api-key-needle" not in applied.text


@pytest.mark.asyncio
async def test_shared_memory_admin_preview_can_scope_one_persona(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _config_path, workspace = _configured_app(tmp_path)
    (workspace / "PROFILE.md").write_text("default-only preference", encoding="utf-8")
    persona_root = workspace / "personas" / "Coder"
    persona_root.mkdir(parents=True)
    (persona_root / "INSIGHTS.md").write_text("coder-only insight", encoding="utf-8")
    cookies = await _login(app)
    page = await _call_route(app, "GET", "/admin/memory/shared", cookies=cookies)
    assert '<option value="Coder">Coder</option>' in page.text
    preview_token = _form_value(page.text, "/admin/memory/shared/preview", "form_token")
    original_build = memory_admin.build_shared_memory_backfill_plan
    captured: dict[str, object] = {}

    def recording_build(workspace_path, config, *, personas=None):
        captured["workspace"] = workspace_path
        captured["personas"] = personas
        return original_build(workspace_path, config, personas=personas)

    monkeypatch.setattr(memory_admin, "build_shared_memory_backfill_plan", recording_build)
    preview = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/preview",
        cookies=cookies,
        data={"form_token": preview_token, "persona": "Coder"},
    )

    assert preview.status == 200
    assert captured == {"workspace": workspace.resolve(), "personas": ["Coder"]}
    assert 'name="persona" value="Coder"' in preview.text
    assert "personas/Coder/INSIGHTS.md" in preview.text
    assert "default-only preference" not in preview.text
    assert "coder-only insight" not in preview.text


@pytest.mark.asyncio
async def test_shared_memory_admin_rejects_stale_or_forged_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _config_path, workspace = _configured_app(tmp_path)
    profile = workspace / "PROFILE.md"
    profile.write_text("previewed preference", encoding="utf-8")
    cookies = await _login(app)
    page = await _call_route(app, "GET", "/admin/memory/shared", cookies=cookies)
    preview_token = _form_value(page.text, "/admin/memory/shared/preview", "form_token")
    preview = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/preview",
        cookies=cookies,
        data={"form_token": preview_token, "persona": ""},
    )
    apply_token = _form_value(preview.text, "/admin/memory/shared/apply", "form_token")
    fingerprint = _form_value(
        preview.text,
        "/admin/memory/shared/apply",
        "plan_fingerprint",
    )

    async def forbidden_execute(*args, **kwargs):
        raise AssertionError("stale or forged confirmation must not execute")

    monkeypatch.setattr(memory_admin, "execute_shared_memory_backfill", forbidden_execute)
    forged = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/apply",
        cookies=cookies,
        data={
            "form_token": "forged",
            "persona": "",
            "plan_fingerprint": fingerprint,
        },
    )
    assert forged.status == 403

    profile.write_text("changed after preview", encoding="utf-8")
    stale = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/apply",
        cookies=cookies,
        data={
            "form_token": apply_token,
            "persona": "",
            "plan_fingerprint": fingerprint,
        },
    )
    assert stale.status == 409
    assert "changed after preview" not in stale.text


@pytest.mark.asyncio
async def test_shared_memory_admin_reports_planning_failure_without_echoing_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _config_path, workspace = _configured_app(tmp_path)
    (workspace / "PROFILE.md").write_text("private-plan-body-needle", encoding="utf-8")
    cookies = await _login(app)
    page = await _call_route(app, "GET", "/admin/memory/shared", cookies=cookies)
    preview_token = _form_value(page.text, "/admin/memory/shared/preview", "form_token")

    def failed_build(*args, **kwargs):
        raise RuntimeError("mem0-api-key-needle private-plan-body-needle")

    monkeypatch.setattr(memory_admin, "build_shared_memory_backfill_plan", failed_build)
    response = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/preview",
        cookies=cookies,
        data={"form_token": preview_token, "persona": ""},
    )

    assert response.status == 500
    assert "mem0-api-key-needle" not in response.text
    assert "private-plan-body-needle" not in response.text


@pytest.mark.asyncio
async def test_shared_memory_admin_reports_apply_failure_without_echoing_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _config_path, workspace = _configured_app(tmp_path)
    (workspace / "PROFILE.md").write_text("private-apply-body-needle", encoding="utf-8")
    cookies = await _login(app)
    page = await _call_route(app, "GET", "/admin/memory/shared", cookies=cookies)
    preview_token = _form_value(page.text, "/admin/memory/shared/preview", "form_token")
    preview = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/preview",
        cookies=cookies,
        data={"form_token": preview_token, "persona": ""},
    )
    apply_token = _form_value(preview.text, "/admin/memory/shared/apply", "form_token")
    fingerprint = _form_value(
        preview.text,
        "/admin/memory/shared/apply",
        "plan_fingerprint",
    )

    async def failed_execute(*args, **kwargs):
        raise RuntimeError("mem0-api-key-needle private-apply-body-needle")

    monkeypatch.setattr(memory_admin, "execute_shared_memory_backfill", failed_execute)
    response = await _call_route(
        app,
        "POST",
        "/admin/memory/shared/apply",
        cookies=cookies,
        data={
            "form_token": apply_token,
            "persona": "",
            "plan_fingerprint": fingerprint,
        },
    )

    assert response.status == 502
    assert "mem0-api-key-needle" not in response.text
    assert "private-apply-body-needle" not in response.text


def test_shared_memory_admin_form_token_is_session_action_binding_and_expiry_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = web.Application()
    from hahobot.gateway.admin.constants import _ADMIN_CONFIG_PATH_KEY

    # Avoid exercising config IO here: token generation needs only the auth
    # helper, which is patched to the same key used by a live authenticated app.
    app[_ADMIN_CONFIG_PATH_KEY] = Path("/unused/config.json")
    request = make_mocked_request(
        "POST",
        "/admin/memory/shared/apply",
        headers={"Cookie": "hahobot_admin_session=session-a"},
        app=app,
    )
    monkeypatch.setattr(memory_admin, "_admin_auth_key", lambda _request: "admin-secret")
    monkeypatch.setattr(memory_admin.time, "time", lambda: 1_000.0)
    token = memory_admin._build_form_token(request, purpose="apply", binding="default\0digest")

    assert memory_admin._is_valid_form_token(
        request,
        token,
        purpose="apply",
        binding="default\0digest",
    )
    assert not memory_admin._is_valid_form_token(
        request,
        token,
        purpose="preview",
        binding="default\0digest",
    )
    assert not memory_admin._is_valid_form_token(
        request,
        token,
        purpose="apply",
        binding="other\0digest",
    )
    other_session = make_mocked_request(
        "POST",
        "/admin/memory/shared/apply",
        headers={"Cookie": "hahobot_admin_session=session-b"},
        app=app,
    )
    assert not memory_admin._is_valid_form_token(
        other_session,
        token,
        purpose="apply",
        binding="default\0digest",
    )
    monkeypatch.setattr(memory_admin.time, "time", lambda: 2_000.0)
    assert not memory_admin._is_valid_form_token(
        request,
        token,
        purpose="apply",
        binding="default\0digest",
    )
