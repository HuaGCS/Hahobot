"""Admin workflow for previewing and applying the local-memory Mem0 backfill."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections import Counter
from pathlib import Path
from typing import Any

from aiohttp import web
from loguru import logger

from hahobot.agent.memory_shared_backfill import (
    SharedMemoryBackfillPlan,
    build_shared_memory_backfill_plan,
    execute_shared_memory_backfill,
    shared_memory_backfill_plan_fingerprint,
    validate_backfill_config,
)
from hahobot.agent.personas import list_personas, resolve_persona_name
from hahobot.config.schema import SharedMemoryConfig
from hahobot.gateway.admin.base import (
    _admin_auth_key,
    _current_config_path,
    _load_current_config,
    _page,
    _require_admin_auth,
    _runtime_workspace,
    _t,
)
from hahobot.gateway.admin.constants import _ADMIN_COOKIE, _LEGACY_ADMIN_COOKIE

_FORM_TOKEN_TTL_SECONDS = 15 * 60
_FORM_TOKEN_CLOCK_SKEW_SECONDS = 5
_PREVIEW_TOKEN_PURPOSE = "shared-memory-backfill-preview"
_APPLY_TOKEN_PURPOSE = "shared-memory-backfill-apply"

_SKIP_REASON_KEYS = {
    "empty_or_boilerplate": "admin_memory_shared_skip_empty",
    "global_write_off": "admin_memory_shared_skip_global_off",
    "inherited_default": "admin_memory_shared_skip_inherited",
    "missing": "admin_memory_shared_skip_missing",
    "persona_disabled": "admin_memory_shared_skip_persona_disabled",
    "read_error": "admin_memory_shared_skip_read_error",
}
_DELIVERY_STATUS_KEYS = {
    "already_imported": "admin_memory_shared_delivery_already_imported",
    "already_queued": "admin_memory_shared_delivery_already_queued",
    "delivered": "admin_memory_shared_delivery_delivered",
    "missing": "admin_memory_shared_delivery_missing",
    "pending": "admin_memory_shared_delivery_pending",
    "queued": "admin_memory_shared_delivery_queued",
}
_RESULT_KEYS = {
    "complete": "admin_memory_shared_result_complete",
    "no_op": "admin_memory_shared_result_no_op",
    "partial": "admin_memory_shared_result_partial",
    "queued": "admin_memory_shared_result_queued",
}


def _session_cookie(request: web.Request) -> str:
    return request.cookies.get(_ADMIN_COOKIE) or request.cookies.get(_LEGACY_ADMIN_COOKIE, "")


def _form_token_payload(
    request: web.Request,
    *,
    purpose: str,
    binding: str,
    expires_at: int,
) -> bytes:
    return (
        f"hahobot-admin-form-v1\0{purpose}\0{binding}\0{expires_at}\0{_session_cookie(request)}"
    ).encode()


def _build_form_token(
    request: web.Request,
    *,
    purpose: str,
    binding: str = "",
) -> str:
    """Build a short-lived action token tied to the authenticated admin session."""
    expires_at = int(time.time()) + _FORM_TOKEN_TTL_SECONDS
    signature = hmac.new(
        _admin_auth_key(request).encode("utf-8"),
        _form_token_payload(
            request,
            purpose=purpose,
            binding=binding,
            expires_at=expires_at,
        ),
        hashlib.sha256,
    ).hexdigest()
    return f"{expires_at}.{signature}"


def _is_valid_form_token(
    request: web.Request,
    token: str,
    *,
    purpose: str,
    binding: str = "",
) -> bool:
    """Validate one action-scoped form token without server-side token storage."""
    expires_raw, separator, signature = token.partition(".")
    if not separator or not signature:
        return False
    try:
        expires_at = int(expires_raw)
    except ValueError:
        return False
    now = int(time.time())
    if (
        expires_at < now
        or expires_at > now + _FORM_TOKEN_TTL_SECONDS + _FORM_TOKEN_CLOCK_SKEW_SECONDS
    ):
        return False
    expected = hmac.new(
        _admin_auth_key(request).encode("utf-8"),
        _form_token_payload(
            request,
            purpose=purpose,
            binding=binding,
            expires_at=expires_at,
        ),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def _backfill_ready(config: SharedMemoryConfig) -> bool:
    try:
        validate_backfill_config(config)
    except ValueError:
        return False
    return True


def _resolve_selection(workspace: Path, raw: object) -> tuple[str, list[str] | None]:
    selected = str(raw or "").strip()
    if not selected:
        return "", None
    resolved = resolve_persona_name(workspace, selected)
    if resolved is None:
        raise ValueError("unknown_persona")
    return resolved, [resolved]


async def _build_plan(
    workspace: Path,
    config: SharedMemoryConfig,
    personas: list[str] | None,
) -> SharedMemoryBackfillPlan:
    return await asyncio.to_thread(
        build_shared_memory_backfill_plan,
        workspace,
        config,
        personas=personas,
    )


def _warning_messages(request: web.Request, plan: SharedMemoryBackfillPlan) -> list[str]:
    historical = 0
    unclosed = 0
    other = 0
    for warning in plan.warnings:
        if warning.startswith("Older local persistence"):
            historical += 1
        elif warning.startswith("Unclosed <"):
            unclosed += 1
        else:
            other += 1
    messages: list[str] = []
    if historical:
        messages.append(_t(request, "admin_memory_shared_warning_historical_privacy"))
    if unclosed:
        messages.append(_t(request, "admin_memory_shared_warning_unclosed", count=unclosed))
    if other:
        messages.append(_t(request, "admin_memory_shared_warning_other", count=other))
    return messages


def _safe_plan_view(request: web.Request, plan: SharedMemoryBackfillPlan) -> dict[str, Any]:
    """Build an explicitly allow-listed view that never carries candidate bodies."""
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in plan.items:
        key = (item.persona, item.source_file, item.layer, item.user_id)
        route = grouped.setdefault(
            key,
            {
                "persona": item.persona,
                "source_file": item.source_file,
                "layer": _t(
                    request,
                    "admin_memory_shared_layer_public"
                    if item.layer == "public"
                    else "admin_memory_shared_layer_persona_private",
                ),
                "user_id": item.user_id,
                "writes": 0,
                "chars": 0,
            },
        )
        route["writes"] += 1
        route["chars"] += len(item.content)

    skipped = [
        {
            "persona": item.persona,
            "source_file": item.source_file,
            "reason": _t(
                request,
                _SKIP_REASON_KEYS.get(item.reason, "admin_memory_shared_skip_other"),
            ),
        }
        for item in plan.skipped
    ]
    return {
        "totals": {
            "files_scanned": plan.files_scanned,
            "files_missing": plan.files_missing,
            "candidate_writes": len(plan.items),
            "candidate_chars": sum(len(item.content) for item in plan.items),
            "public_writes": sum(item.layer == "public" for item in plan.items),
            "persona_private_writes": sum(item.layer == "persona_private" for item in plan.items),
            "skipped": len(plan.skipped),
        },
        "routes": sorted(
            grouped.values(),
            key=lambda route: (
                str(route["persona"]).casefold(),
                str(route["source_file"]),
                str(route["layer"]),
            ),
        ),
        "skipped": skipped,
        "warnings": _warning_messages(request, plan),
    }


def _safe_result_view(
    request: web.Request,
    *,
    mode: str,
    statuses: dict[str, str],
) -> dict[str, Any]:
    counts = Counter(statuses.values())
    rows = [
        {
            "status": _t(
                request,
                _DELIVERY_STATUS_KEYS.get(status, "admin_memory_shared_delivery_other"),
            ),
            "count": count,
        }
        for status, count in sorted(counts.items())
    ]
    return {
        "mode": mode,
        "message": _t(
            request,
            _RESULT_KEYS.get(mode, "admin_memory_shared_result_partial"),
        ),
        "statuses": rows,
    }


def _render_shared_memory_page(
    request: web.Request,
    *,
    selected_persona: str = "",
    plan: SharedMemoryBackfillPlan | None = None,
    plan_fingerprint: str = "",
    result: dict[str, Any] | None = None,
    allow_apply: bool = False,
    error_key: str | None = None,
    status: int = 200,
) -> web.Response:
    config = _load_current_config(request)
    shared = config.memory.shared
    workspace = _runtime_workspace(request).expanduser().resolve(strict=False)
    ready = _backfill_ready(shared)
    preview_token = _build_form_token(request, purpose=_PREVIEW_TOKEN_PURPOSE) if ready else ""
    apply_token = ""
    if allow_apply and plan is not None and plan.items and plan_fingerprint:
        apply_token = _build_form_token(
            request,
            purpose=_APPLY_TOKEN_PURPOSE,
            binding=f"{selected_persona}\0{plan_fingerprint}",
        )

    response = _page(
        template_name="gateway/admin/memory_shared.html",
        title=_t(request, "admin_memory_shared_title"),
        heading=_t(request, "admin_memory_shared_heading"),
        request=request,
        error=_t(request, error_key) if error_key else None,
        memory_nav_label=_t(request, "admin_memory_shared_nav"),
        intro=_t(request, "admin_memory_shared_intro"),
        workspace_label=_t(request, "admin_meta_workspace"),
        workspace_path=str(workspace),
        ready=ready,
        ready_label=_t(
            request,
            "admin_memory_shared_ready" if ready else "admin_memory_shared_not_ready",
        ),
        configure_label=_t(request, "admin_memory_shared_configure"),
        public_user_label=_t(request, "admin_memory_shared_public_user"),
        public_user_id=shared.user_id.strip() if ready else "-",
        persona_mode_label=_t(request, "admin_memory_shared_persona_mode"),
        persona_mode_text=_t(
            request,
            "admin_boolean_true" if shared.persona_enabled else "admin_boolean_false",
        ),
        selection_title=_t(request, "admin_memory_shared_selection_title"),
        selection_desc=_t(request, "admin_memory_shared_selection_desc"),
        persona_label=_t(request, "admin_memory_shared_persona"),
        all_personas_label=_t(request, "admin_memory_shared_all_personas"),
        personas=list_personas(workspace),
        selected_persona=selected_persona,
        preview_token=preview_token,
        preview_label=_t(request, "admin_memory_shared_preview_button"),
        preview=None if plan is None else _safe_plan_view(request, plan),
        preview_title=_t(request, "admin_memory_shared_preview_title"),
        preview_desc=_t(request, "admin_memory_shared_preview_desc"),
        files_scanned_label=_t(request, "admin_memory_shared_files_scanned"),
        files_missing_label=_t(request, "admin_memory_shared_files_missing"),
        candidate_writes_label=_t(request, "admin_memory_shared_candidate_writes"),
        candidate_chars_label=_t(request, "admin_memory_shared_candidate_chars"),
        public_writes_label=_t(request, "admin_memory_shared_public_writes"),
        private_writes_label=_t(request, "admin_memory_shared_private_writes"),
        skipped_label=_t(request, "admin_memory_shared_skipped"),
        warnings_title=_t(request, "admin_memory_shared_warnings_title"),
        routes_title=_t(request, "admin_memory_shared_routes_title"),
        route_writes_label=_t(request, "admin_memory_shared_route_writes"),
        route_chars_label=_t(request, "admin_memory_shared_route_chars"),
        skipped_title=_t(request, "admin_memory_shared_skipped_title"),
        no_candidates_label=_t(request, "admin_memory_shared_no_candidates"),
        allow_apply=bool(apply_token),
        apply_token=apply_token,
        plan_fingerprint=plan_fingerprint,
        apply_label=_t(request, "admin_memory_shared_apply_button"),
        apply_confirm=_t(request, "admin_memory_shared_apply_confirm"),
        result=result,
        result_title=_t(request, "admin_memory_shared_result_title"),
        delivery_title=_t(request, "admin_memory_shared_delivery_title"),
    )
    response.set_status(status)
    return response


async def _admin_shared_memory_page(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    return _render_shared_memory_page(request)


async def _admin_shared_memory_preview(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    form = await request.post()
    token = str(form.get("form_token", ""))
    if not _is_valid_form_token(request, token, purpose=_PREVIEW_TOKEN_PURPOSE):
        return _render_shared_memory_page(
            request,
            error_key="admin_memory_shared_invalid_token",
            status=403,
        )

    config = _load_current_config(request)
    shared = config.memory.shared
    if not _backfill_ready(shared):
        return _render_shared_memory_page(
            request,
            error_key="admin_memory_shared_not_ready_error",
            status=409,
        )
    workspace = _runtime_workspace(request).expanduser().resolve(strict=False)
    try:
        selected, personas = _resolve_selection(workspace, form.get("persona"))
    except ValueError:
        return _render_shared_memory_page(
            request,
            error_key="admin_memory_shared_unknown_persona",
            status=400,
        )
    try:
        plan = await _build_plan(workspace, shared, personas)
    except Exception:
        logger.exception("Failed to build the Admin shared-memory backfill preview")
        return _render_shared_memory_page(
            request,
            selected_persona=selected,
            error_key="admin_memory_shared_preview_failed",
            status=500,
        )
    fingerprint = shared_memory_backfill_plan_fingerprint(plan, shared)
    return _render_shared_memory_page(
        request,
        selected_persona=selected,
        plan=plan,
        plan_fingerprint=fingerprint,
        allow_apply=True,
    )


async def _admin_shared_memory_apply(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    form = await request.post()
    selected_raw = str(form.get("persona", "")).strip()
    fingerprint = str(form.get("plan_fingerprint", "")).strip()
    token = str(form.get("form_token", ""))
    if not fingerprint or not _is_valid_form_token(
        request,
        token,
        purpose=_APPLY_TOKEN_PURPOSE,
        binding=f"{selected_raw}\0{fingerprint}",
    ):
        return _render_shared_memory_page(
            request,
            error_key="admin_memory_shared_invalid_token",
            status=403,
        )

    config = _load_current_config(request)
    shared = config.memory.shared
    if not _backfill_ready(shared):
        return _render_shared_memory_page(
            request,
            selected_persona=selected_raw,
            error_key="admin_memory_shared_not_ready_error",
            status=409,
        )
    workspace = _runtime_workspace(request).expanduser().resolve(strict=False)
    try:
        selected, personas = _resolve_selection(workspace, selected_raw)
    except ValueError:
        return _render_shared_memory_page(
            request,
            error_key="admin_memory_shared_unknown_persona",
            status=400,
        )
    try:
        plan = await _build_plan(workspace, shared, personas)
    except Exception:
        logger.exception("Failed to rebuild the Admin shared-memory backfill plan")
        return _render_shared_memory_page(
            request,
            selected_persona=selected,
            error_key="admin_memory_shared_preview_failed",
            status=500,
        )
    rebuilt_fingerprint = shared_memory_backfill_plan_fingerprint(plan, shared)
    if not hmac.compare_digest(fingerprint, rebuilt_fingerprint):
        return _render_shared_memory_page(
            request,
            selected_persona=selected,
            plan=plan,
            error_key="admin_memory_shared_stale_preview",
            status=409,
        )

    state_root = (
        _current_config_path(request).expanduser().resolve(strict=False).parent / "shared-memory"
    )
    try:
        mode, statuses = await execute_shared_memory_backfill(
            plan,
            shared,
            state_root=state_root,
        )
    except Exception:
        logger.exception("Failed to apply the Admin shared-memory backfill plan")
        return _render_shared_memory_page(
            request,
            selected_persona=selected,
            plan=plan,
            error_key="admin_memory_shared_apply_failed",
            status=502,
        )
    return _render_shared_memory_page(
        request,
        selected_persona=selected,
        plan=plan,
        result=_safe_result_view(request, mode=mode, statuses=statuses),
    )
