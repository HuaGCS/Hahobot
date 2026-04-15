"""Bootstrap helper for companion-oriented persona workspaces."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from hahobot.agent.personas import (
    DEFAULT_PERSONA,
    PERSONA_METADATA_DIRNAME,
    PERSONA_ST_MANIFEST_FILENAME,
    PERSONAS_DIRNAME,
    normalize_persona_name,
)
from hahobot.config.schema import Config
from hahobot.utils.helpers import safe_filename, sync_workspace_templates

_DEFAULT_HEARTBEAT_TASK = (
    "- [ ] Companion check-in: if the user has been quiet for a while and context suggests it would "
    "be welcome, send one short, specific, low-pressure message in the active persona. Skip if "
    "they were active recently or asked for space."
)


@dataclass(frozen=True)
class CompanionInitResult:
    """Summary of files written during companion bootstrap."""

    workspace: Path
    persona: str
    persona_dir: Path
    created_paths: tuple[Path, ...]
    updated_paths: tuple[Path, ...]
    skipped_paths: tuple[Path, ...]
    copied_assets: tuple[Path, ...]
    heartbeat_task_added: bool


def _companion_soul(name: str) -> str:
    return (
        "# Soul\n\n"
        f"You are {name}, a warm, grounded long-term companion.\n\n"
        "## Core Traits\n\n"
        "- Warm, attentive, and emotionally perceptive without being dramatic.\n"
        "- Natural and low-pressure; never clingy, theatrical, or manipulative.\n"
        "- Comfortable switching between emotional support, everyday chat, and practical help.\n"
        "- Specific and observant; refer to recent context instead of generic care phrases.\n"
    )


def _companion_user(name: str) -> str:
    return (
        "# Relationship\n\n"
        f"This persona relates to the user as {name}: a trusted, steady companion.\n\n"
        "## Default Stance\n\n"
        "- Prioritize empathy before advice when the user sounds tired, stressed, or vulnerable.\n"
        "- Keep check-ins short, natural, and context-aware.\n"
        "- Match the user's energy instead of forcing intimacy or depth.\n\n"
        "## Boundaries\n\n"
        "- Do not pressure the user to disclose feelings.\n"
        "- Skip proactive care if the user asked for space or was active recently.\n"
        "- Stay honest about uncertainty and avoid therapeutic role-play claims.\n"
    )


def _companion_style() -> str:
    return (
        "# Companion Style\n\n"
        "- Prefer short, natural replies over long speeches.\n"
        "- Ask gentle follow-up questions only when they help.\n"
        "- Celebrate wins specifically; comfort setbacks calmly.\n"
        "- Avoid repetitive catchphrases and generic wellness scripts.\n"
    )


def _companion_voice(name: str) -> str:
    return json.dumps(
        {
            "instructions": (
                f"Speak as {name}. Keep the delivery warm, calm, natural, and low-pressure."
            )
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def _companion_manifest(*, reference_image: str | None = None) -> dict[str, object]:
    manifest: dict[str, object] = {
        "scene_prompts": {
            "daily": (
                "Keep the atmosphere casual and lived-in. Prefer believable everyday details over "
                "stylized influencer shots."
            ),
            "comfort": (
                "Keep physical distance and touch gentle, emotionally supportive, and realistic. "
                "Avoid melodrama or exaggerated sadness."
            ),
            "date": (
                "Prefer intimate, grounded date settings that fit the persona rather than luxury "
                "spectacle."
            ),
        }
    }
    if reference_image:
        manifest["reference_image"] = reference_image
    return manifest


def _manifest_path(persona_dir: Path) -> Path:
    return persona_dir / PERSONA_METADATA_DIRNAME / PERSONA_ST_MANIFEST_FILENAME


def _read_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Persona manifest must be a JSON object: {path}")
    return data


def _record_write(
    path: Path,
    content: str,
    *,
    force: bool,
    created: list[Path],
    updated: list[Path],
    skipped: list[Path],
) -> None:
    if path.exists() and not force:
        skipped.append(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.write_text(content, encoding="utf-8")
        updated.append(path)
    else:
        path.write_text(content, encoding="utf-8")
        created.append(path)


def _append_heartbeat_task(
    heartbeat_path: Path,
    *,
    created: list[Path],
    updated: list[Path],
) -> bool:
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    if not heartbeat_path.exists():
        heartbeat_path.write_text("# Heartbeat Tasks\n\n## Active Tasks\n\n## Completed\n", encoding="utf-8")
        created.append(heartbeat_path)
    content = heartbeat_path.read_text(encoding="utf-8")
    if _DEFAULT_HEARTBEAT_TASK in content:
        return False

    if "\n## Completed" in content:
        content = content.replace("\n## Completed", f"\n{_DEFAULT_HEARTBEAT_TASK}\n\n## Completed", 1)
    else:
        suffix = "" if content.endswith("\n") else "\n"
        content = f"{content}{suffix}\n{_DEFAULT_HEARTBEAT_TASK}\n"
    heartbeat_path.write_text(content, encoding="utf-8")
    if heartbeat_path not in created and heartbeat_path not in updated:
        updated.append(heartbeat_path)
    return True


def _copy_reference_image(source: Path, asset_dir: Path) -> tuple[Path, str]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    target = asset_dir / safe_filename(source.name or "reference.png")
    if source.resolve(strict=False) != target.resolve(strict=False):
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            index = 2
            while True:
                candidate = asset_dir / f"{stem}_{index}{suffix}"
                if not candidate.exists():
                    target = candidate
                    break
                index += 1
        shutil.copy2(source, target)
    return target, str(target.relative_to(asset_dir.parent))


def _merge_missing_dict(target: dict, defaults: dict) -> dict:
    for key, value in defaults.items():
        if isinstance(value, dict):
            existing = target.get(key)
            if isinstance(existing, dict):
                _merge_missing_dict(existing, value)
            else:
                target[key] = dict(value)
        else:
            target.setdefault(key, value)
    return target


def init_companion_workspace(
    config: Config,
    *,
    persona: str | None,
    force: bool = False,
    reference_image: str | None = None,
    add_heartbeat_task: bool = True,
) -> CompanionInitResult:
    """Create a minimal companion persona scaffold inside the active workspace."""
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    sync_workspace_templates(workspace, silent=True)

    normalized = normalize_persona_name(persona) if persona is not None else DEFAULT_PERSONA
    if normalized is None:
        raise ValueError(f"Invalid persona name: {persona}")

    persona_name = normalized
    persona_dir = workspace if persona_name == DEFAULT_PERSONA else workspace / PERSONAS_DIRNAME / persona_name
    persona_dir.mkdir(parents=True, exist_ok=True)

    display_name = "default companion persona" if persona_name == DEFAULT_PERSONA else persona_name
    created: list[Path] = []
    updated: list[Path] = []
    skipped: list[Path] = []
    copied_assets: list[Path] = []

    _record_write(
        persona_dir / "SOUL.md",
        _companion_soul(display_name),
        force=force,
        created=created,
        updated=updated,
        skipped=skipped,
    )
    _record_write(
        persona_dir / "USER.md",
        _companion_user(display_name),
        force=force,
        created=created,
        updated=updated,
        skipped=skipped,
    )
    _record_write(
        persona_dir / "STYLE.md",
        _companion_style(),
        force=force,
        created=created,
        updated=updated,
        skipped=skipped,
    )
    _record_write(
        persona_dir / "VOICE.json",
        _companion_voice(display_name),
        force=force,
        created=created,
        updated=updated,
        skipped=skipped,
    )

    manifest_path = _manifest_path(persona_dir)
    manifest_defaults = _companion_manifest()
    if manifest_path.exists():
        if force:
            manifest_path.write_text(
                json.dumps(manifest_defaults, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if manifest_path not in updated:
                updated.append(manifest_path)
        else:
            manifest = _read_manifest(manifest_path)
            before = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
            merged = _merge_missing_dict(manifest, manifest_defaults)
            after = json.dumps(merged, ensure_ascii=False, sort_keys=True)
            if after != before:
                manifest_path.write_text(
                    json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                if manifest_path not in updated:
                    updated.append(manifest_path)
            else:
                if manifest_path not in skipped:
                    skipped.append(manifest_path)
    else:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest_defaults, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        created.append(manifest_path)

    heartbeat_added = False
    if add_heartbeat_task:
        heartbeat_added = _append_heartbeat_task(
            workspace / "HEARTBEAT.md",
            created=created,
            updated=updated,
        )

    if reference_image:
        source = Path(reference_image).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise ValueError(f"Reference image not found: {source}")
        copied_path, manifest_reference = _copy_reference_image(source, persona_dir / "assets")
        if copied_path.exists():
            copied_assets.append(copied_path)
        manifest = _read_manifest(manifest_path)
        merged_manifest = _merge_missing_dict(
            manifest,
            _companion_manifest(reference_image=manifest_reference),
        )
        merged_manifest["reference_image"] = manifest_reference
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(merged_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if manifest_path not in created and manifest_path not in updated:
            updated.append(manifest_path)

    return CompanionInitResult(
        workspace=workspace,
        persona=persona_name,
        persona_dir=persona_dir,
        created_paths=tuple(created),
        updated_paths=tuple(updated),
        skipped_paths=tuple(skipped),
        copied_assets=tuple(copied_assets),
        heartbeat_task_added=heartbeat_added,
    )
