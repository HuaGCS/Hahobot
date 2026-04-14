"""Internal runtime tool-policy helpers."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hahobot.config.schema import ExecToolConfig, ImageGenConfig, WebToolsConfig

PolicyStatus = Literal["ok", "warn", "disabled"]


@dataclass(frozen=True)
class ToolPolicyDecision:
    """One runtime decision for a tool family."""

    tool_name: str
    enabled: bool
    status: PolicyStatus
    summary: str
    detail: str = ""
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolWorkspaceScope:
    """Resolved filesystem scope for workspace-bound tools."""

    allowed_dir: Path | None
    extra_read_dirs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class RuntimeToolPolicy:
    """Centralized policy decisions derived from current runtime config."""

    workspace: Path
    restrict_to_workspace: bool
    web_config: WebToolsConfig
    exec_config: ExecToolConfig
    image_gen_config: ImageGenConfig
    builtin_read_dirs: tuple[Path, ...] = ()

    def workspace_scope(self) -> ToolWorkspaceScope:
        """Resolve the allowed filesystem root shared by workspace tools."""
        sandboxed = bool((self.exec_config.sandbox or "").strip())
        allowed_dir = self.workspace if (self.restrict_to_workspace or sandboxed) else None
        extra_read_dirs = self.builtin_read_dirs if allowed_dir else ()
        return ToolWorkspaceScope(allowed_dir=allowed_dir, extra_read_dirs=extra_read_dirs)

    def web(self) -> ToolPolicyDecision:
        """Return the current web-tools decision."""
        web_cfg = self.web_config
        search_cfg = web_cfg.search
        detail_parts = [f"provider={search_cfg.provider}", f"max_results={search_cfg.max_results}"]
        if web_cfg.proxy:
            detail_parts.append(f"proxy={web_cfg.proxy}")

        if not web_cfg.enable:
            return ToolPolicyDecision(
                tool_name="web",
                enabled=False,
                status="disabled",
                summary="Web tools are disabled.",
                detail=" ".join(detail_parts),
            )

        issues: list[str] = []
        if search_cfg.provider == "brave" and not search_cfg.api_key.strip():
            issues.append("Brave search is selected but tools.web.search.apiKey is empty.")
        if search_cfg.provider == "searxng" and not search_cfg.base_url.strip():
            issues.append("SearXNG search is selected but tools.web.search.baseUrl is empty.")
        if search_cfg.provider == "searxng" and search_cfg.base_url.strip():
            detail_parts.append(f"base_url={search_cfg.base_url}")
        if search_cfg.provider == "duckduckgo":
            detail_parts.append("serialized=true")

        return ToolPolicyDecision(
            tool_name="web",
            enabled=True,
            status="ok" if not issues else "warn",
            summary="Web tools are ready." if not issues else "Web tools are enabled with incomplete search settings.",
            detail=" ".join(detail_parts),
            issues=tuple(issues),
        )

    def exec(self) -> ToolPolicyDecision:
        """Return the current exec-tool decision."""
        exec_cfg = self.exec_config
        detail_parts = [f"timeout={exec_cfg.timeout}s"]
        if exec_cfg.sandbox:
            detail_parts.append(f"sandbox={exec_cfg.sandbox}")
        if exec_cfg.path_append:
            detail_parts.append(f"path_append={exec_cfg.path_append}")
        if exec_cfg.allowed_env_keys:
            detail_parts.append(f"allowed_env_keys={len(exec_cfg.allowed_env_keys)}")

        if not exec_cfg.enable:
            return ToolPolicyDecision(
                tool_name="exec",
                enabled=False,
                status="disabled",
                summary="Exec tool is disabled.",
                detail=" ".join(detail_parts),
            )

        issues: list[str] = []
        if exec_cfg.sandbox == "bwrap" and shutil.which("bwrap") is None:
            issues.append("Exec sandbox is set to bwrap, but `bwrap` is not available in PATH.")

        return ToolPolicyDecision(
            tool_name="exec",
            enabled=True,
            status="ok" if not issues else "warn",
            summary="Exec tool is ready." if not issues else "Exec tool is enabled but sandbox prerequisites are missing.",
            detail=" ".join(detail_parts),
            issues=tuple(issues),
        )

    def image_gen(self) -> ToolPolicyDecision:
        """Return the current image-generation decision."""
        image_cfg = self.image_gen_config
        detail_parts = [f"model={image_cfg.model}", f"base_url={image_cfg.base_url}"]

        if not image_cfg.enabled:
            return ToolPolicyDecision(
                tool_name="image_gen",
                enabled=False,
                status="disabled",
                summary="Image generation tool is disabled.",
                detail=" ".join(detail_parts),
            )

        issues: list[str] = []
        if not image_cfg.api_key.strip():
            issues.append(
                "Image generation is enabled without tools.imageGen.apiKey. "
                "This is only valid if the configured endpoint does not require authentication."
            )

        return ToolPolicyDecision(
            tool_name="image_gen",
            enabled=True,
            status="ok" if not issues else "warn",
            summary=(
                "Image generation tool is ready."
                if not issues
                else "Image generation is enabled but authentication may be incomplete."
            ),
            detail=" ".join(detail_parts),
            issues=tuple(issues),
        )
