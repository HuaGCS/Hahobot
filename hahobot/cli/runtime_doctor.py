"""Read-only runtime diagnostics and CLI summaries."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hahobot.config.schema import Config
from hahobot.providers.registry import find_by_name

Status = Literal["ok", "warn", "fail"]

_BOOTSTRAP_FILES = ("AGENTS.md", "SOUL.md", "USER.md", "memory/MEMORY.md")
_CHANNEL_NAMES = (
    "whatsapp",
    "telegram",
    "discord",
    "feishu",
    "mochat",
    "dingtalk",
    "email",
    "slack",
    "qq",
    "matrix",
    "weixin",
    "wecom",
)


@dataclass(frozen=True)
class RuntimeCheck:
    """One read-only runtime doctor check."""

    id: str
    status: Status
    summary: str
    detail: str = ""
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "summary": self.summary,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class ModelTargetSummary:
    """One concrete provider target in the active route."""

    provider: str
    provider_label: str
    model: str
    status: Status
    api_base: str | None = None
    detail: str = ""
    issues: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status != "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_label": self.provider_label,
            "model": self.model,
            "status": self.status,
            "ready": self.ready,
            "api_base": self.api_base,
            "detail": self.detail,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class ModelSummary:
    """Summary of the active model routing state."""

    config_path: Path | None
    workspace: Path
    route_mode: Literal["single", "provider_pool"]
    selection_mode: Literal["auto", "forced", "provider_pool"]
    model: str
    provider: str | None
    provider_label: str | None
    api_base: str | None
    reasoning_effort: str | None
    temperature: float
    max_tokens: int
    context_window_tokens: int
    max_tool_iterations: int
    status: Status
    detail: str = ""
    provider_pool_strategy: str | None = None
    targets: tuple[ModelTargetSummary, ...] = ()
    issues: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status != "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_path": str(self.config_path) if self.config_path is not None else None,
            "workspace": str(self.workspace),
            "route_mode": self.route_mode,
            "selection_mode": self.selection_mode,
            "model": self.model,
            "provider": self.provider,
            "provider_label": self.provider_label,
            "api_base": self.api_base,
            "reasoning_effort": self.reasoning_effort,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "context_window_tokens": self.context_window_tokens,
            "max_tool_iterations": self.max_tool_iterations,
            "status": self.status,
            "ready": self.ready,
            "detail": self.detail,
            "provider_pool_strategy": self.provider_pool_strategy,
            "targets": [target.to_dict() for target in self.targets],
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class ToolSummary:
    """One tool family summary."""

    enabled: bool
    status: Status
    summary: str
    detail: str = ""
    issues: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status != "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "ready": self.ready,
            "summary": self.summary,
            "detail": self.detail,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class MCPServerSummary:
    """One MCP server entry."""

    name: str
    transport: str
    status: Status
    detail: str = ""
    issues: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status != "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "status": self.status,
            "ready": self.ready,
            "detail": self.detail,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class MCPOverview:
    """Summary of all configured MCP servers."""

    status: Status
    summary: str
    detail: str = ""
    servers: tuple[MCPServerSummary, ...] = ()

    @property
    def server_count(self) -> int:
        return len(self.servers)

    @property
    def ready_count(self) -> int:
        return sum(server.status == "ok" for server in self.servers)

    @property
    def warn_count(self) -> int:
        return sum(server.status == "warn" for server in self.servers)

    @property
    def fail_count(self) -> int:
        return sum(server.status == "fail" for server in self.servers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "detail": self.detail,
            "server_count": self.server_count,
            "ready_count": self.ready_count,
            "warn_count": self.warn_count,
            "fail_count": self.fail_count,
            "servers": [server.to_dict() for server in self.servers],
        }


@dataclass(frozen=True)
class ToolsSummary:
    """Summary of the active tool configuration."""

    config_path: Path | None
    workspace: Path
    restrict_to_workspace: bool
    web: ToolSummary
    exec: ToolSummary
    image_gen: ToolSummary
    mcp: MCPOverview

    @property
    def overall_status(self) -> Status:
        statuses = (self.web.status, self.exec.status, self.image_gen.status, self.mcp.status)
        if "fail" in statuses:
            return "fail"
        if "warn" in statuses:
            return "warn"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_path": str(self.config_path) if self.config_path is not None else None,
            "workspace": str(self.workspace),
            "restrict_to_workspace": self.restrict_to_workspace,
            "overall_status": self.overall_status,
            "web": self.web.to_dict(),
            "exec": self.exec.to_dict(),
            "image_gen": self.image_gen.to_dict(),
            "mcp": self.mcp.to_dict(),
        }


@dataclass(frozen=True)
class RuntimeDoctorReport:
    """Serializable top-level runtime readiness report."""

    config_path: Path | None
    workspace: Path
    checks: tuple[RuntimeCheck, ...]

    @property
    def ok_count(self) -> int:
        return sum(check.status == "ok" for check in self.checks)

    @property
    def warn_count(self) -> int:
        return sum(check.status == "warn" for check in self.checks)

    @property
    def fail_count(self) -> int:
        return sum(check.status == "fail" for check in self.checks)

    @property
    def overall_status(self) -> Status:
        if self.fail_count:
            return "fail"
        if self.warn_count:
            return "warn"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_path": str(self.config_path) if self.config_path is not None else None,
            "workspace": str(self.workspace),
            "overall_status": self.overall_status,
            "ok_count": self.ok_count,
            "warn_count": self.warn_count,
            "fail_count": self.fail_count,
            "checks": [check.to_dict() for check in self.checks],
        }


def _check(
    check_id: str,
    status: Status,
    summary: str,
    *,
    detail: str = "",
    fix: str = "",
) -> RuntimeCheck:
    return RuntimeCheck(
        id=check_id,
        status=status,
        summary=summary,
        detail=detail,
        fix=fix,
    )


def _format_status(status: Status) -> str:
    return status.upper()


def _iter_enabled_channels(config: Config) -> list[str]:
    enabled: list[str] = []
    for name in _CHANNEL_NAMES:
        section = getattr(config.channels, name, None)
        if section is None:
            continue
        instances = getattr(section, "instances", None)
        if isinstance(instances, list):
            active = [
                instance.name
                for instance in instances
                if isinstance(getattr(instance, "name", None), str)
                and getattr(instance, "enabled", True)
            ]
            if active:
                enabled.extend(f"{name}/{instance_name}" for instance_name in active)
            elif getattr(section, "enabled", False):
                enabled.append(name)
            continue
        if getattr(section, "enabled", False):
            enabled.append(name)
    return enabled


def _provider_target_status(config: Config, provider_name: str | None, model: str) -> ModelTargetSummary:
    if not provider_name:
        return ModelTargetSummary(
            provider="unresolved",
            provider_label="Unresolved",
            model=model,
            status="fail",
            detail="No provider could be resolved from the current configuration.",
            issues=("No provider resolved.",),
        )

    spec = find_by_name(provider_name)
    if spec is None:
        return ModelTargetSummary(
            provider=provider_name,
            provider_label=provider_name,
            model=model,
            status="fail",
            detail="The configured provider name is unknown.",
            issues=(f"Unknown provider: {provider_name}.",),
        )

    provider_cfg = getattr(config.providers, spec.name, None)
    api_key = (getattr(provider_cfg, "api_key", "") or "").strip()
    raw_api_base = (getattr(provider_cfg, "api_base", "") or "").strip()
    api_base = raw_api_base or (spec.default_api_base if spec.is_gateway or spec.is_local else None)
    issues: list[str] = []
    detail_parts: list[str] = []

    if spec.backend == "azure_openai":
        if not api_key:
            issues.append("Missing api_key.")
        if not raw_api_base:
            issues.append("Missing api_base.")
    elif spec.is_oauth:
        detail_parts.append("OAuth provider; local token state is not verified by this command.")
    elif spec.is_direct:
        if not api_base:
            issues.append("Missing api_base.")
    elif spec.is_local:
        if not api_base:
            issues.append("Missing api_base.")
    elif not api_key:
        issues.append("Missing api_key.")

    if api_base:
        detail_parts.append(f"Endpoint: {api_base}")

    if issues:
        return ModelTargetSummary(
            provider=spec.name,
            provider_label=spec.label,
            model=model,
            status="fail",
            api_base=api_base,
            detail=" ".join(detail_parts).strip(),
            issues=tuple(issues),
        )

    return ModelTargetSummary(
        provider=spec.name,
        provider_label=spec.label,
        model=model,
        status="ok",
        api_base=api_base,
        detail=" ".join(detail_parts).strip(),
    )


def build_model_summary(config: Config) -> ModelSummary:
    defaults = config.agents.defaults
    config_path = config._config_path
    workspace = config.workspace_path
    provider_pool = defaults.provider_pool

    if provider_pool and provider_pool.targets:
        targets = tuple(
            _provider_target_status(
                config,
                target.provider,
                target.model or defaults.model,
            )
            for target in provider_pool.targets
        )
        issues = tuple(
            issue
            for target in targets
            for issue in target.issues
        )
        status: Status = "ok" if not issues else "fail"
        detail = (
            f"Provider pool strategy: {provider_pool.strategy}. "
            f"{len(targets)} target(s) configured."
        )
        return ModelSummary(
            config_path=config_path,
            workspace=workspace,
            route_mode="provider_pool",
            selection_mode="provider_pool",
            model=defaults.model,
            provider=None,
            provider_label=None,
            api_base=None,
            reasoning_effort=defaults.reasoning_effort,
            temperature=defaults.temperature,
            max_tokens=defaults.max_tokens,
            context_window_tokens=defaults.context_window_tokens,
            max_tool_iterations=defaults.max_tool_iterations,
            status=status,
            detail=detail,
            provider_pool_strategy=provider_pool.strategy,
            targets=targets,
            issues=issues,
        )

    forced_provider = defaults.provider if defaults.provider != "auto" else None
    resolved_provider = forced_provider or config.get_provider_name(defaults.model)
    target = _provider_target_status(config, resolved_provider, defaults.model)
    selection_mode: Literal["auto", "forced", "provider_pool"] = "forced" if forced_provider else "auto"
    detail_parts = []
    if selection_mode == "forced":
        detail_parts.append(f"Provider pinned by config: {defaults.provider}")
    else:
        detail_parts.append("Provider resolved from model/config heuristics.")
    if target.detail:
        detail_parts.append(target.detail)

    return ModelSummary(
        config_path=config_path,
        workspace=workspace,
        route_mode="single",
        selection_mode=selection_mode,
        model=defaults.model,
        provider=target.provider,
        provider_label=target.provider_label,
        api_base=target.api_base,
        reasoning_effort=defaults.reasoning_effort,
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        context_window_tokens=defaults.context_window_tokens,
        max_tool_iterations=defaults.max_tool_iterations,
        status=target.status,
        detail=" ".join(part for part in detail_parts if part).strip(),
        targets=(target,),
        issues=target.issues,
    )


def _build_web_summary(config: Config) -> ToolSummary:
    web_cfg = config.tools.web
    search_cfg = web_cfg.search
    detail_parts = [f"provider={search_cfg.provider}", f"max_results={search_cfg.max_results}"]
    if web_cfg.proxy:
        detail_parts.append(f"proxy={web_cfg.proxy}")

    if not web_cfg.enable:
        return ToolSummary(
            enabled=False,
            status="ok",
            summary="Web tool is disabled.",
            detail=" ".join(detail_parts),
        )

    issues: list[str] = []
    if search_cfg.provider == "brave" and not search_cfg.api_key.strip():
        issues.append("Brave search is selected but tools.web.search.apiKey is empty.")
    if search_cfg.provider == "searxng" and not search_cfg.base_url.strip():
        issues.append("SearXNG search is selected but tools.web.search.baseUrl is empty.")
    if search_cfg.provider == "searxng" and search_cfg.base_url.strip():
        detail_parts.append(f"base_url={search_cfg.base_url}")

    status: Status = "ok" if not issues else "warn"
    summary = "Web tool is ready." if not issues else "Web tool is enabled with incomplete search settings."
    return ToolSummary(
        enabled=True,
        status=status,
        summary=summary,
        detail=" ".join(detail_parts),
        issues=tuple(issues),
    )


def _build_exec_summary(config: Config) -> ToolSummary:
    exec_cfg = config.tools.exec
    detail_parts = [f"timeout={exec_cfg.timeout}s"]
    if exec_cfg.sandbox:
        detail_parts.append(f"sandbox={exec_cfg.sandbox}")
    if exec_cfg.path_append:
        detail_parts.append(f"path_append={exec_cfg.path_append}")

    if not exec_cfg.enable:
        return ToolSummary(
            enabled=False,
            status="ok",
            summary="Exec tool is disabled.",
            detail=" ".join(detail_parts),
        )

    issues: list[str] = []
    if exec_cfg.sandbox == "bwrap" and shutil.which("bwrap") is None:
        issues.append("Exec sandbox is set to bwrap, but `bwrap` is not available in PATH.")

    status: Status = "ok" if not issues else "warn"
    summary = "Exec tool is ready." if not issues else "Exec tool is enabled but sandbox prerequisites are missing."
    return ToolSummary(
        enabled=True,
        status=status,
        summary=summary,
        detail=" ".join(detail_parts),
        issues=tuple(issues),
    )


def _build_image_gen_summary(config: Config) -> ToolSummary:
    image_cfg = config.tools.image_gen
    detail_parts = [f"model={image_cfg.model}", f"base_url={image_cfg.base_url}"]

    if not image_cfg.enabled:
        return ToolSummary(
            enabled=False,
            status="ok",
            summary="Image generation tool is disabled.",
            detail=" ".join(detail_parts),
        )

    issues: list[str] = []
    if not image_cfg.api_key.strip():
        issues.append(
            "Image generation is enabled without tools.imageGen.apiKey. "
            "This is only valid if the configured endpoint does not require authentication."
        )

    status: Status = "ok" if not issues else "warn"
    summary = (
        "Image generation tool is ready."
        if not issues
        else "Image generation is enabled but authentication may be incomplete."
    )
    return ToolSummary(
        enabled=True,
        status=status,
        summary=summary,
        detail=" ".join(detail_parts),
        issues=tuple(issues),
    )


def _detect_transport(server: Any) -> str:
    if server.type:
        return server.type
    if server.command:
        return "stdio"
    if server.url:
        return "streamableHttp"
    return "unknown"


def _build_mcp_overview(config: Config) -> MCPOverview:
    if not config.tools.mcp_servers:
        return MCPOverview(
            status="ok",
            summary="No MCP servers configured.",
        )

    servers: list[MCPServerSummary] = []
    for name, server in config.tools.mcp_servers.items():
        transport = _detect_transport(server)
        issues: list[str] = []
        detail_parts: list[str] = []
        if transport == "stdio":
            if not server.command.strip():
                issues.append("Missing command.")
            else:
                command = server.command.strip()
                if Path(command).is_absolute():
                    if not Path(command).exists():
                        issues.append(f"Command path does not exist: {command}")
                elif shutil.which(command) is None:
                    issues.append(f"Command not found in PATH: {command}")
                detail_parts.append(f"command={command}")
                if server.args:
                    detail_parts.append(f"args={len(server.args)}")
        elif transport in {"sse", "streamableHttp"}:
            if not server.url.strip():
                issues.append("Missing url.")
            else:
                detail_parts.append(f"url={server.url}")
        else:
            issues.append("Cannot infer transport from config.")

        status: Status = "ok" if not issues else "warn"
        servers.append(
            MCPServerSummary(
                name=name,
                transport=transport,
                status=status,
                detail=" ".join(detail_parts),
                issues=tuple(issues),
            )
        )

    overall: Status = "ok"
    if any(server.status == "fail" for server in servers):
        overall = "fail"
    elif any(server.status == "warn" for server in servers):
        overall = "warn"

    summary = (
        f"{len(servers)} MCP server(s) configured."
        if overall == "ok"
        else f"{len(servers)} MCP server(s) configured, with warnings."
    )
    return MCPOverview(
        status=overall,
        summary=summary,
        servers=tuple(servers),
    )


def build_tools_summary(config: Config) -> ToolsSummary:
    return ToolsSummary(
        config_path=config._config_path,
        workspace=config.workspace_path,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        web=_build_web_summary(config),
        exec=_build_exec_summary(config),
        image_gen=_build_image_gen_summary(config),
        mcp=_build_mcp_overview(config),
    )


def run_runtime_doctor(config: Config) -> RuntimeDoctorReport:
    config_path = config._config_path
    workspace = config.workspace_path
    model_summary = build_model_summary(config)
    tools_summary = build_tools_summary(config)
    checks: list[RuntimeCheck] = []

    config_exists = bool(config_path and config_path.exists())
    checks.append(
        _check(
            "config_file",
            "ok" if config_exists else "warn",
            "Config file found." if config_exists else "Config file does not exist yet.",
            detail=str(config_path) if config_path is not None else "",
            fix="Run `hahobot onboard` to create and save a config file."
            if not config_exists
            else "",
        )
    )

    workspace_exists = workspace.exists()
    checks.append(
        _check(
            "workspace",
            "ok" if workspace_exists else "warn",
            "Workspace directory exists." if workspace_exists else "Workspace directory does not exist yet.",
            detail=str(workspace),
            fix="Run `hahobot onboard` or `hahobot agent` once to seed the workspace."
            if not workspace_exists
            else "",
        )
    )

    missing_bootstrap = [name for name in _BOOTSTRAP_FILES if not (workspace / name).exists()]
    checks.append(
        _check(
            "workspace_bootstrap",
            "ok" if not missing_bootstrap else "warn",
            "Workspace bootstrap files look complete."
            if not missing_bootstrap
            else "Workspace bootstrap files are incomplete.",
            detail=", ".join(missing_bootstrap) if missing_bootstrap else "",
            fix="Run `hahobot onboard` to create the missing workspace templates."
            if missing_bootstrap
            else "",
        )
    )

    checks.append(
        _check(
            "model_route",
            model_summary.status,
            "Model route is ready." if model_summary.ready else "Model route is incomplete.",
            detail=model_summary.detail or "; ".join(model_summary.issues),
            fix=(
                "Set `agents.defaults.provider` / `agents.defaults.model`, then add the required "
                "provider credentials under `providers.*`."
            )
            if not model_summary.ready
            else "",
        )
    )

    enabled_channels = _iter_enabled_channels(config)
    checks.append(
        _check(
            "channels",
            "ok" if enabled_channels else "warn",
            "At least one chat channel is enabled."
            if enabled_channels
            else "No chat channels are enabled.",
            detail=", ".join(enabled_channels) if enabled_channels else "CLI and OpenAI-compatible API can still be used.",
            fix="Enable a channel under `channels.*` if you want gateway delivery."
            if not enabled_channels
            else "",
        )
    )

    checks.append(
        _check(
            "web_tool",
            tools_summary.web.status,
            tools_summary.web.summary,
            detail=tools_summary.web.detail or "; ".join(tools_summary.web.issues),
            fix=(
                "Configure `tools.web.search.apiKey` for Brave or `tools.web.search.baseUrl` for SearXNG."
            )
            if tools_summary.web.issues
            else "",
        )
    )
    checks.append(
        _check(
            "exec_tool",
            tools_summary.exec.status,
            tools_summary.exec.summary,
            detail=tools_summary.exec.detail or "; ".join(tools_summary.exec.issues),
            fix="Install `bwrap` or disable `tools.exec.sandbox`."
            if tools_summary.exec.issues
            else "",
        )
    )
    checks.append(
        _check(
            "image_gen",
            tools_summary.image_gen.status,
            tools_summary.image_gen.summary,
            detail=tools_summary.image_gen.detail or "; ".join(tools_summary.image_gen.issues),
            fix="Set `tools.imageGen.apiKey` or point `tools.imageGen.baseUrl` at an unauthenticated local endpoint."
            if tools_summary.image_gen.issues
            else "",
        )
    )
    checks.append(
        _check(
            "mcp_servers",
            tools_summary.mcp.status,
            tools_summary.mcp.summary,
            detail=tools_summary.mcp.detail
            or "; ".join(
                f"{server.name}: {', '.join(server.issues)}"
                for server in tools_summary.mcp.servers
                if server.issues
            ),
            fix="Complete each MCP server's `command` or `url` configuration."
            if tools_summary.mcp.status != "ok"
            else "",
        )
    )

    return RuntimeDoctorReport(
        config_path=config_path,
        workspace=workspace,
        checks=tuple(checks),
    )


def render_runtime_doctor_text(report: RuntimeDoctorReport) -> str:
    lines = [
        "hahobot doctor",
        "",
        f"Overall: {_format_status(report.overall_status)}",
        f"Config: {report.config_path}",
        f"Workspace: {report.workspace}",
        "",
    ]
    for check in report.checks:
        lines.append(f"[{_format_status(check.status)}] {check.id}: {check.summary}")
        if check.detail:
            lines.append(f"  detail: {check.detail}")
        if check.fix:
            lines.append(f"  fix: {check.fix}")
    return "\n".join(lines)


def render_model_summary_text(summary: ModelSummary) -> str:
    lines = [
        "hahobot model",
        "",
        f"Route mode: {summary.route_mode}",
        f"Selection: {summary.selection_mode}",
        f"Default model: {summary.model}",
        f"Reasoning effort: {summary.reasoning_effort or 'none'}",
        f"Temperature: {summary.temperature}",
        f"Max tokens: {summary.max_tokens:,}",
        f"Context window: {summary.context_window_tokens:,}",
        f"Max tool iterations: {summary.max_tool_iterations:,}",
        f"Status: {_format_status(summary.status)}",
    ]
    if summary.route_mode == "provider_pool":
        lines.append(f"Provider pool strategy: {summary.provider_pool_strategy}")
        if summary.detail:
            lines.append(f"Detail: {summary.detail}")
        lines.append("")
        lines.append("Targets:")
        for target in summary.targets:
            line = f"- [{_format_status(target.status)}] {target.provider_label} -> {target.model}"
            if target.api_base:
                line += f" ({target.api_base})"
            lines.append(line)
            if target.detail:
                lines.append(f"  detail: {target.detail}")
            if target.issues:
                lines.append(f"  issues: {'; '.join(target.issues)}")
        return "\n".join(lines)

    lines.append(f"Provider: {summary.provider_label or summary.provider or 'unresolved'}")
    if summary.api_base:
        lines.append(f"Endpoint: {summary.api_base}")
    if summary.detail:
        lines.append(f"Detail: {summary.detail}")
    if summary.issues:
        lines.append(f"Issues: {'; '.join(summary.issues)}")
    return "\n".join(lines)


def render_tools_summary_text(summary: ToolsSummary) -> str:
    lines = [
        "hahobot tools",
        "",
        f"Overall: {_format_status(summary.overall_status)}",
        f"Restrict to workspace: {'yes' if summary.restrict_to_workspace else 'no'}",
        "",
        f"Web: [{_format_status(summary.web.status)}] {summary.web.summary}",
    ]
    if summary.web.detail:
        lines.append(f"  detail: {summary.web.detail}")
    if summary.web.issues:
        lines.append(f"  issues: {'; '.join(summary.web.issues)}")

    lines.append(f"Exec: [{_format_status(summary.exec.status)}] {summary.exec.summary}")
    if summary.exec.detail:
        lines.append(f"  detail: {summary.exec.detail}")
    if summary.exec.issues:
        lines.append(f"  issues: {'; '.join(summary.exec.issues)}")

    lines.append(f"Image Gen: [{_format_status(summary.image_gen.status)}] {summary.image_gen.summary}")
    if summary.image_gen.detail:
        lines.append(f"  detail: {summary.image_gen.detail}")
    if summary.image_gen.issues:
        lines.append(f"  issues: {'; '.join(summary.image_gen.issues)}")

    lines.append(f"MCP: [{_format_status(summary.mcp.status)}] {summary.mcp.summary}")
    if summary.mcp.detail:
        lines.append(f"  detail: {summary.mcp.detail}")
    for server in summary.mcp.servers:
        line = f"  - [{_format_status(server.status)}] {server.name} ({server.transport})"
        if server.detail:
            line += f" {server.detail}"
        lines.append(line)
        if server.issues:
            lines.append(f"    issues: {'; '.join(server.issues)}")
    return "\n".join(lines)
