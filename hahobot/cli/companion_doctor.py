"""Read-only diagnostics for companion-oriented workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hahobot.agent.personas import (
    DEFAULT_PERSONA,
    PERSONA_SOUL_FILENAME,
    PERSONA_USER_FILENAME,
    list_personas,
    load_persona_reference_images,
    load_persona_scene_settings,
    persona_workspace,
    resolve_persona_name,
)
from hahobot.config.schema import Config

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
class CompanionCheck:
    """One doctor check result."""

    id: str
    status: Literal["ok", "warn", "fail"]
    summary: str
    detail: str = ""
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-ready representation."""
        return {
            "id": self.id,
            "status": self.status,
            "summary": self.summary,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class CompanionDoctorReport:
    """Serializable companion doctor report."""

    config_path: Path | None
    workspace: Path
    requested_persona: str | None
    persona: str
    checks: tuple[CompanionCheck, ...]

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
    def overall_status(self) -> Literal["ok", "warn", "fail"]:
        if self.fail_count:
            return "fail"
        if self.warn_count:
            return "warn"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-ready representation."""
        return {
            "config_path": str(self.config_path) if self.config_path is not None else None,
            "workspace": str(self.workspace),
            "requested_persona": self.requested_persona,
            "persona": self.persona,
            "overall_status": self.overall_status,
            "ok_count": self.ok_count,
            "warn_count": self.warn_count,
            "fail_count": self.fail_count,
            "checks": [check.to_dict() for check in self.checks],
        }


def _check(
    check_id: str,
    status: Literal["ok", "warn", "fail"],
    summary: str,
    *,
    detail: str = "",
    fix: str = "",
) -> CompanionCheck:
    return CompanionCheck(
        id=check_id,
        status=status,
        summary=summary,
        detail=detail,
        fix=fix,
    )


def _has_active_heartbeat_tasks(content: str) -> bool:
    """Return True when HEARTBEAT.md contains more than headings/comments."""
    in_comment = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if line.startswith("<!--"):
            if not line.endswith("-->"):
                in_comment = True
            continue
        if line.startswith("#"):
            continue
        return True
    return False


def _enabled_channels(config: Config) -> list[str]:
    """Return enabled channel names or instance routes."""
    enabled: list[str] = []
    for name in _CHANNEL_NAMES:
        section = getattr(config.channels, name, None)
        if section is None:
            continue
        instances = getattr(section, "instances", None)
        if isinstance(instances, list):
            active_instances = [
                instance.name
                for instance in instances
                if isinstance(getattr(instance, "name", None), str)
                and getattr(instance, "enabled", True)
            ]
            if getattr(section, "enabled", False) or active_instances:
                if active_instances:
                    enabled.extend(f"{name}/{instance_name}" for instance_name in active_instances)
                else:
                    enabled.append(name)
            continue
        if getattr(section, "enabled", False):
            enabled.append(name)
    return enabled


def _resolve_target_persona(workspace: Path, requested: str | None) -> tuple[str, str | None, list[str]]:
    """Resolve the persona to inspect and return available custom personas."""
    personas = list_personas(workspace)
    custom_personas = [persona for persona in personas if persona != DEFAULT_PERSONA]
    if requested:
        return requested, resolve_persona_name(workspace, requested), custom_personas
    if custom_personas:
        return custom_personas[0], custom_personas[0], custom_personas
    return DEFAULT_PERSONA, DEFAULT_PERSONA, custom_personas


def _voice_provider_check(config: Config) -> CompanionCheck:
    """Validate that the current TTS provider has the minimum required config."""
    voice = config.channels.voice_reply
    if not voice.enabled:
        return _check(
            "voice_reply",
            "warn",
            "Voice reply is disabled.",
            detail="Companion flows can still work, but they will stay text-only.",
            fix="Enable channels.voiceReply.enabled if you want persona-aware voice replies.",
        )

    configured_channels = [name for name in voice.channels if isinstance(name, str) and name.strip()]
    if not configured_channels:
        return _check(
            "voice_reply",
            "warn",
            "Voice reply is enabled, but no target channels are configured.",
            fix="Set channels.voiceReply.channels to one or more outbound channels such as telegram.",
        )

    provider_name = voice.provider
    if provider_name == "edge":
        return _check(
            "voice_provider",
            "ok",
            "Voice reply provider is ready: edge.",
            detail=f"Targets: {', '.join(configured_channels)}",
        )

    if provider_name == "sovits":
        missing_fields: list[str] = []
        if not voice.sovits_api_url.strip():
            missing_fields.append("channels.voiceReply.sovitsApiUrl")
        if not voice.sovits_refer_wav_path.strip():
            missing_fields.append("channels.voiceReply.sovitsReferWavPath")
        if not voice.sovits_prompt_text.strip():
            missing_fields.append("channels.voiceReply.sovitsPromptText")
        if missing_fields:
            return _check(
                "voice_provider",
                "fail",
                "Voice reply provider is incomplete: sovits.",
                detail=f"Missing: {', '.join(missing_fields)}",
                fix="Configure the missing GPT-SoVITS fields before enabling voice replies.",
            )
        return _check(
            "voice_provider",
            "ok",
            "Voice reply provider is ready: sovits.",
            detail=f"Targets: {', '.join(configured_channels)}",
        )

    provider_cfg, provider_name_hint = config._match_provider()
    api_key = (voice.api_key or getattr(provider_cfg, "api_key", "") or "").strip()
    api_base = (voice.api_base or getattr(provider_cfg, "api_base", "") or "https://api.openai.com/v1").strip()
    if not api_key:
        return _check(
            "voice_provider",
            "fail",
            "Voice reply provider is incomplete: openai.",
            detail=(
                "Neither channels.voiceReply.apiKey nor the active chat provider api_key is configured."
            ),
            fix="Set channels.voiceReply.apiKey or configure an API key on the active conversation provider.",
        )

    detail = f"Targets: {', '.join(configured_channels)} | Endpoint: {api_base}"
    if provider_name_hint:
        detail += f" | Chat provider fallback: {provider_name_hint}"
    return _check(
        "voice_provider",
        "ok",
        "Voice reply provider is ready: openai.",
        detail=detail,
    )


def _reference_image_check(config: Config, workspace: Path, persona: str | None) -> CompanionCheck:
    """Validate persona reference-image configuration for companion image flows."""
    images = load_persona_reference_images(workspace, persona)
    paths = [value for value in [images.default, *images.scenes.values()] if value]
    if not paths:
        return _check(
            "reference_images",
            "warn",
            "No persona reference images are configured.",
            fix="Set reference_image or reference_images in the persona's .hahobot/st_manifest.json.",
        )

    workspace_root = workspace.resolve(strict=False)
    missing: list[str] = []
    outside: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        resolved = path.resolve(strict=False)
        if not resolved.exists() or not resolved.is_file():
            missing.append(str(resolved))
            continue
        if not resolved.is_relative_to(workspace_root):
            outside.append(str(resolved))

    if missing:
        return _check(
            "reference_images",
            "fail",
            "Persona reference images are configured, but one or more files are missing.",
            detail="\n".join(missing),
            fix="Update the manifest paths or restore the missing image files under the workspace.",
        )
    if outside and config.tools.restrict_to_workspace:
        return _check(
            "reference_images",
            "fail",
            "Persona reference images resolve outside the workspace while tool access is restricted.",
            detail="\n".join(outside),
            fix="Move the images into the workspace or disable tools.restrictToWorkspace.",
        )
    if outside:
        return _check(
            "reference_images",
            "warn",
            "Persona reference images resolve outside the workspace.",
            detail="\n".join(outside),
            fix="Prefer storing companion reference images inside the workspace for portability.",
        )
    return _check(
        "reference_images",
        "ok",
        f"Persona reference images are ready ({len(paths)} file(s)).",
    )


def _scene_shortcut_check(config: Config, workspace: Path, persona: str | None) -> CompanionCheck:
    """Validate whether the /scene shortcut has persona-local guidance configured."""
    settings = load_persona_scene_settings(workspace, persona)
    images = load_persona_reference_images(workspace, persona)
    scene_refs = images.scenes
    prompt_keys = sorted(settings.prompts)
    caption_keys = sorted(settings.captions)
    ref_keys = sorted(scene_refs)

    detail_parts: list[str] = []
    if images.default:
        detail_parts.append("default reference image: configured")
    if ref_keys:
        detail_parts.append(f"scene references: {', '.join(ref_keys)}")
    if prompt_keys:
        detail_parts.append(f"prompt overrides: {', '.join(prompt_keys)}")
    if caption_keys:
        detail_parts.append(f"caption overrides: {', '.join(caption_keys)}")
    detail = " | ".join(detail_parts)

    if not config.tools.image_gen.enabled:
        return _check(
            "scene_shortcuts",
            "warn",
            "/scene shortcuts are configured but image generation is disabled.",
            detail=detail,
            fix="Enable tools.imageGen.enabled if you want /scene to generate actual images.",
        )

    if images.default or ref_keys or prompt_keys or caption_keys:
        return _check(
            "scene_shortcuts",
            "ok",
            "/scene shortcuts have persona-local scene guidance configured.",
            detail=detail,
        )

    return _check(
        "scene_shortcuts",
        "warn",
        "/scene will use only the built-in fallback templates.",
        fix="Add scene_prompts, scene_captions, or scene-specific reference_images to the persona manifest if you want stronger scene consistency.",
    )


def run_companion_doctor(config: Config, *, persona: str | None = None) -> CompanionDoctorReport:
    """Inspect the active workspace and report companion readiness."""
    workspace = config.workspace_path.resolve(strict=False)
    requested_persona = persona.strip() if isinstance(persona, str) and persona.strip() else None
    target_name, resolved_persona, custom_personas = _resolve_target_persona(workspace, requested_persona)
    checks: list[CompanionCheck] = []

    if workspace.exists() and workspace.is_dir():
        checks.append(
            _check(
                "workspace",
                "ok",
                "Workspace directory exists.",
                detail=str(workspace),
            )
        )
    else:
        checks.append(
            _check(
                "workspace",
                "fail",
                "Workspace directory is missing.",
                detail=str(workspace),
                fix="Run `hahobot onboard` or pass --workspace to point at an existing workspace.",
            )
        )

    if custom_personas:
        checks.append(
            _check(
                "personas",
                "ok",
                f"Found {len(custom_personas)} custom persona(s).",
                detail=", ".join(custom_personas),
            )
        )
    else:
        checks.append(
            _check(
                "personas",
                "warn",
                "No custom personas found; only the default workspace persona is available.",
                fix="Import a SillyTavern card or create personas/<name>/SOUL.md for a dedicated companion persona.",
            )
        )

    if requested_persona and resolved_persona is None:
        available = ", ".join(list_personas(workspace))
        checks.append(
            _check(
                "persona_target",
                "fail",
                f"Requested persona not found: {requested_persona}.",
                detail=f"Available personas: {available}",
                fix="Use --persona with one of the listed names, or import the target persona first.",
            )
        )
    else:
        checks.append(
            _check(
                "persona_target",
                "ok",
                f"Doctor target persona: {resolved_persona or target_name}.",
                detail=(
                    "Auto-selected first custom persona."
                    if requested_persona is None and resolved_persona not in (None, DEFAULT_PERSONA)
                    else ""
                ),
            )
        )

    active_persona = resolved_persona or target_name
    active_root = persona_workspace(workspace, active_persona) if resolved_persona else workspace / "personas" / target_name
    has_soul = (active_root / PERSONA_SOUL_FILENAME).exists()
    has_user = (active_root / PERSONA_USER_FILENAME).exists()
    if resolved_persona is not None and (has_soul or has_user):
        checks.append(
            _check(
                "persona_files",
                "ok",
                "Persona prompt files are present.",
                detail=f"SOUL.md={has_soul} USER.md={has_user}",
            )
        )
    elif resolved_persona is None:
        checks.append(
            _check(
                "persona_files",
                "fail",
                "Persona prompt files could not be checked because the target persona does not exist.",
                detail=str(active_root),
                fix="Create the persona directory and add SOUL.md or USER.md.",
            )
        )
    else:
        checks.append(
            _check(
                "persona_files",
                "fail",
                "Persona prompt files are missing.",
                detail=str(active_root),
                fix="Add SOUL.md or USER.md to the target persona workspace.",
            )
        )

    enabled_channels = _enabled_channels(config)
    if enabled_channels:
        checks.append(
            _check(
                "channels",
                "ok",
                f"At least one channel is enabled ({len(enabled_channels)}).",
                detail=", ".join(enabled_channels),
            )
        )
    else:
        checks.append(
            _check(
                "channels",
                "warn",
                "No outbound chat channel is enabled.",
                fix="Enable at least one channel such as telegram, discord, or weixin.",
            )
        )

    heartbeat = config.gateway.heartbeat
    if heartbeat.enabled:
        checks.append(
            _check(
                "heartbeat",
                "ok",
                f"Heartbeat is enabled every {heartbeat.interval_s}s.",
                detail=f"keepRecentMessages={heartbeat.keep_recent_messages}",
            )
        )
    else:
        checks.append(
            _check(
                "heartbeat",
                "warn",
                "Heartbeat is disabled.",
                fix="Enable gateway.heartbeat.enabled for proactive companion check-ins.",
            )
        )

    heartbeat_file = workspace / "HEARTBEAT.md"
    if not heartbeat_file.exists():
        checks.append(
            _check(
                "heartbeat_file",
                "warn",
                "HEARTBEAT.md is missing.",
                detail=str(heartbeat_file),
                fix="Create HEARTBEAT.md if you want scheduled companion follow-up prompts.",
            )
        )
    else:
        try:
            heartbeat_content = heartbeat_file.read_text(encoding="utf-8")
        except OSError:
            heartbeat_content = ""
        if not heartbeat_content.strip():
            checks.append(
                _check(
                    "heartbeat_file",
                    "warn",
                    "HEARTBEAT.md is empty.",
                    detail=str(heartbeat_file),
                    fix="Add at least one active task or reminder to HEARTBEAT.md.",
                )
            )
        elif not _has_active_heartbeat_tasks(heartbeat_content):
            checks.append(
                _check(
                    "heartbeat_file",
                    "warn",
                    "HEARTBEAT.md has no active tasks.",
                    detail=str(heartbeat_file),
                    fix="Add a task below the Active Tasks section if proactive care should do anything.",
                )
            )
        else:
            checks.append(
                _check(
                    "heartbeat_file",
                    "ok",
                    "HEARTBEAT.md contains active tasks.",
                    detail=str(heartbeat_file),
                )
            )

    checks.append(_voice_provider_check(config))

    image_gen = config.tools.image_gen
    if image_gen.enabled:
        checks.append(
            _check(
                "image_gen",
                "ok",
                f"Image generation is enabled with model {image_gen.model}.",
            )
        )
    else:
        checks.append(
            _check(
                "image_gen",
                "warn",
                "Image generation is disabled.",
                fix="Enable tools.imageGen.enabled if you want reference-image companion scenes.",
            )
        )

    checks.append(_reference_image_check(config, workspace, resolved_persona))
    checks.append(_scene_shortcut_check(config, workspace, resolved_persona))

    return CompanionDoctorReport(
        config_path=getattr(config, "_config_path", None),
        workspace=workspace,
        requested_persona=requested_persona,
        persona=active_persona,
        checks=tuple(checks),
    )


def render_companion_doctor_text(report: CompanionDoctorReport) -> str:
    """Render a human-readable doctor report."""
    lines = [
        "hahobot Companion Doctor",
        f"Config: {report.config_path or '(default)'}",
        f"Workspace: {report.workspace}",
        f"Persona: {report.persona}",
        f"Overall: {report.overall_status.upper()} (ok={report.ok_count} warn={report.warn_count} fail={report.fail_count})",
        "",
    ]
    for check in report.checks:
        lines.append(f"[{check.status.upper()}] {check.id}: {check.summary}")
        if check.detail:
            lines.append(f"  {check.detail}")
        if check.fix:
            lines.append(f"  Fix: {check.fix}")
    return "\n".join(lines)
