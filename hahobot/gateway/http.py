"""Minimal HTTP server for gateway health checks."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path

from aiohttp import web
from loguru import logger
from markupsafe import Markup

from hahobot.config.loader import load_config
from hahobot.gateway.admin import register_admin_routes, update_admin_runtime_workspace
from hahobot.gateway.runtime_status import GatewayRuntimeStatusTracker
from hahobot.heartbeat.service import HeartbeatService, HeartbeatStatusSnapshot
from hahobot.star_office import StarOfficeStatusTracker
from hahobot.utils.html_templates import render_html_template

_STAR_OFFICE_TRACKER_KEY = web.AppKey("star_office_tracker", object)
_RUNTIME_STATUS_TRACKER_KEY = web.AppKey("runtime_status_tracker", object)
_HEARTBEAT_SERVICE_KEY = web.AppKey("heartbeat_service", object)


def _status_tracker(app: web.Application) -> StarOfficeStatusTracker | None:
    tracker = app.get(_STAR_OFFICE_TRACKER_KEY)
    return tracker if isinstance(tracker, StarOfficeStatusTracker) else None


def _runtime_status_tracker(app: web.Application) -> GatewayRuntimeStatusTracker | None:
    tracker = app.get(_RUNTIME_STATUS_TRACKER_KEY)
    return tracker if isinstance(tracker, GatewayRuntimeStatusTracker) else None


def _heartbeat_service(app: web.Application) -> HeartbeatService | None:
    service = app.get(_HEARTBEAT_SERVICE_KEY)
    return service if isinstance(service, HeartbeatService) else None


def _is_authorized(request: web.Request, auth_key: str) -> bool:
    if not auth_key:
        return True
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    token = auth_header.removeprefix("Bearer ").strip()
    return hmac.compare_digest(token, auth_key)


def _wants_html(request: web.Request) -> bool:
    format_arg = request.query.get("format", "").strip().lower()
    if format_arg == "html":
        return True
    if format_arg == "json":
        return False

    accept = request.headers.get("Accept", "").lower()
    return "text/html" in accept and "application/json" not in accept


def _default_heartbeat_snapshot() -> HeartbeatStatusSnapshot:
    return HeartbeatStatusSnapshot(
        enabled=False,
        running=False,
        model="",
        interval_s=0,
        last_status="unavailable",
        last_detail="Heartbeat status unavailable",
        last_checked_at=None,
        last_checked_at_ms=None,
    )


def _status_badge_class(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"ok", "idle", "running"}:
        return "badge ok"
    if normalized in {"checking", "skipped"}:
        return "badge warn"
    return "badge err"


def _runtime_health_text(health: str) -> str:
    return "正常运行" if health == "ok" else "运行异常"


def _task_status_text(status: str) -> str:
    mapping = {
        "running": "处理中",
        "ok": "已完成",
        "error": "失败",
    }
    return mapping.get(status, status or "未知")


def _heartbeat_status_text(status: str) -> str:
    mapping = {
        "disabled": "已关闭",
        "idle": "等待首次检测",
        "checking": "检测中",
        "missing": "未找到 HEARTBEAT.md",
        "skipped": "无待处理任务",
        "running": "执行中",
        "ok": "最近一次成功",
        "error": "最近一次失败",
        "unavailable": "不可用",
    }
    return mapping.get(status, status or "未知")


def _render_status_page(
    *,
    runtime_snapshot,
    heartbeat_snapshot: HeartbeatStatusSnapshot,
) -> str:
    task = runtime_snapshot.recent_task
    task_html = (
        (
            f'<div class="stack">'
            f'<div class="task-title">{escape(task.summary)}</div>'
            f'<div class="meta-row"><span class="{_status_badge_class(task.status)}">{escape(_task_status_text(task.status))}</span>'
            f'<span>开始于 <code>{escape(task.started_at or "-")}</code></span></div>'
            f'<div class="meta-row"><span>结束于 <code>{escape(task.finished_at or "仍在处理中")}</code></span></div>'
            f'<div class="muted">{escape(task.response_preview or "暂无响应摘要")}</div>'
            f"</div>"
        )
        if task is not None
        else '<div class="muted">当前实例启动后还没有处理过任务。</div>'
    )
    heartbeat_model = heartbeat_snapshot.model or runtime_snapshot.model or "unknown"
    heartbeat_interval = (
        f"{heartbeat_snapshot.interval_s}s"
        if heartbeat_snapshot.interval_s > 0
        else "未配置"
    )
    return render_html_template(
        "gateway/status.html",
        runtime_health_text=_runtime_health_text(runtime_snapshot.health),
        current_state_badge_class=_status_badge_class(runtime_snapshot.current_state),
        current_state_text=runtime_snapshot.current_state,
        active_runs=runtime_snapshot.active_runs,
        current_detail=runtime_snapshot.current_detail or "暂无详细状态",
        current_model=runtime_snapshot.model or "unknown",
        started_at=runtime_snapshot.started_at,
        started_at_ms=runtime_snapshot.started_at_ms,
        task_html=Markup(task_html),
        heartbeat_badge_class=_status_badge_class(heartbeat_snapshot.last_status),
        heartbeat_status_text=_heartbeat_status_text(heartbeat_snapshot.last_status),
        heartbeat_model=heartbeat_model,
        heartbeat_enabled_text="开启" if heartbeat_snapshot.enabled else "关闭",
        heartbeat_running_text="运行中" if heartbeat_snapshot.running else "未运行",
        heartbeat_interval=heartbeat_interval,
        heartbeat_checked_at=heartbeat_snapshot.last_checked_at or "-",
        heartbeat_detail=heartbeat_snapshot.last_detail or "暂无 heartbeat 检测记录",
    )


def create_http_app(
    *,
    config_path: Path | None = None,
    workspace: Path | None = None,
    reload_runtime: Callable[[], Awaitable[None]] | None = None,
    star_office_tracker: StarOfficeStatusTracker | None = None,
    runtime_status_tracker: GatewayRuntimeStatusTracker | None = None,
    heartbeat_service: HeartbeatService | None = None,
) -> web.Application:
    """Create the gateway HTTP app."""
    app = web.Application()
    if config_path is not None and star_office_tracker is None:
        star_office_tracker = StarOfficeStatusTracker()
    if config_path is not None and runtime_status_tracker is None:
        runtime_status_tracker = GatewayRuntimeStatusTracker()
    if star_office_tracker is not None:
        app[_STAR_OFFICE_TRACKER_KEY] = star_office_tracker
    if runtime_status_tracker is not None:
        app[_RUNTIME_STATUS_TRACKER_KEY] = runtime_status_tracker
    if heartbeat_service is not None:
        app[_HEARTBEAT_SERVICE_KEY] = heartbeat_service

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def status(request: web.Request) -> web.Response:
        if config_path is None:
            raise web.HTTPNotFound()

        config = load_config(config_path)
        status_cfg = config.gateway.status
        if not status_cfg.enabled:
            raise web.HTTPNotFound()
        if not _is_authorized(request, status_cfg.auth_key):
            return web.json_response(
                {"error": "unauthorized"},
                status=401,
                headers={"WWW-Authenticate": 'Bearer realm="hahobot-status"'},
            )

        tracker = _status_tracker(request.app)
        star_snapshot = (
            tracker.snapshot()
            if tracker is not None
            else StarOfficeStatusTracker().snapshot()
        )
        if _wants_html(request):
            runtime_tracker = _runtime_status_tracker(request.app) or GatewayRuntimeStatusTracker()
            heartbeat_snapshot = (
                _heartbeat_service(request.app).snapshot()
                if _heartbeat_service(request.app) is not None
                else _default_heartbeat_snapshot()
            )
            html = _render_status_page(
                runtime_snapshot=runtime_tracker.snapshot(star_snapshot),
                heartbeat_snapshot=heartbeat_snapshot,
            )
            return web.Response(text=html, content_type="text/html")
        payload = star_snapshot.to_payload()
        return web.json_response(payload)

    app.router.add_get("/healthz", health)
    if config_path is not None:
        app.router.add_get("/status", status)
    if config_path is not None and workspace is not None:
        register_admin_routes(
            app,
            config_path=config_path,
            workspace=workspace,
            reload_runtime=reload_runtime,
        )
    return app


class GatewayHttpServer:
    """Small aiohttp server exposing health checks."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        config_path: Path | None = None,
        workspace: Path | None = None,
        reload_runtime: Callable[[], Awaitable[None]] | None = None,
        star_office_tracker: StarOfficeStatusTracker | None = None,
        runtime_status_tracker: GatewayRuntimeStatusTracker | None = None,
        heartbeat_service: HeartbeatService | None = None,
    ):
        self.host = host
        self.port = port
        self._app = create_http_app(
            config_path=config_path,
            workspace=workspace,
            reload_runtime=reload_runtime,
            star_office_tracker=star_office_tracker,
            runtime_status_tracker=runtime_status_tracker,
            heartbeat_service=heartbeat_service,
        )
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    def update_runtime_workspace(self, workspace: Path) -> None:
        """Update the admin UI runtime-workspace pointer after a hot reload."""
        update_admin_runtime_workspace(self._app, workspace)

    async def start(self) -> None:
        """Start serving the HTTP routes."""
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self.host, port=self.port)
        await self._site.start()
        logger.info(
            "Gateway HTTP server listening on {}:{} (/healthz, optional /status, optional /admin)",
            self.host,
            self.port,
        )

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
