"""Persona and scene editor pages for the admin UI."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from html import escape
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import web

from hahobot.agent.commands.scene import (
    available_scene_names,
    build_scene_generation_spec,
    extract_generated_path,
    render_scene_caption,
)
from hahobot.agent.memory_metadata import summarize_memory_metadata
from hahobot.agent.personas import (
    DEFAULT_PERSONA,
    PERSONA_METADATA_DIRNAME,
    PERSONA_ST_MANIFEST_FILENAME,
    PERSONA_VOICE_FILENAME,
    list_personas,
    normalize_persona_name,
    normalize_response_filter_tags,
    persona_workspace,
    personas_root,
    resolve_persona_name,
)
from hahobot.agent.tools.image_gen import ImageGenTool
from hahobot.gateway.admin.base import (
    _admin_language,
    _load_current_config,
    _markup,
    _page,
    _pretty_json,
    _read_json_text,
    _read_text,
    _redirect,
    _require_admin_auth,
    _runtime_workspace,
    _t,
    _th,
)
from hahobot.gateway.admin.constants import (
    _LEGACY_USER_INSIGHTS_SECTION_TITLES,
    _LEGACY_USER_PROFILE_SECTION_TITLES,
    _LEGACY_USER_PROFILE_TITLE_RE,
    _LEGACY_USER_RELATIONSHIP_SECTION_TITLES,
    _SCENE_NAME_RE,
)
from hahobot.utils.helpers import detect_image_mime, ensure_dir


@dataclass(frozen=True)
class PersonaScenePreview:
    """Admin-side scene preview result."""

    scene_name: str
    brief: str
    caption: str
    image_path: Path
    image_data_url: str | None


def _scene_preview_image_data_url(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    mime = detect_image_mime(raw) or "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _scene_preview_form_defaults(request: web.Request, persona: str) -> dict[str, str]:
    scenes = available_scene_names(_runtime_workspace(request), persona)
    return {
        "scene_name": scenes[0] if scenes else "daily",
        "scene_brief": "",
    }


def _scene_template_form_defaults(
    request: web.Request,
    persona: str,
    *,
    preview: PersonaScenePreview | None = None,
    preview_form: dict[str, str] | None = None,
) -> dict[str, str]:
    if preview is None:
        defaults = _scene_preview_form_defaults(request, persona)
        return {
            "scene_name": defaults["scene_name"],
            "scene_prompt": "",
            "scene_caption": "",
        }
    persona_label = persona if persona != DEFAULT_PERSONA else _t(request, "scene_persona_fallback")
    caption = (
        preview.caption.replace(persona_label, "{persona}") if persona_label else preview.caption
    )
    return {
        "scene_name": preview.scene_name,
        "scene_prompt": (preview_form or {}).get("scene_brief", ""),
        "scene_caption": caption,
    }


def _validate_scene_name(request: web.Request, name: str) -> str:
    normalized = name.strip()
    if not _SCENE_NAME_RE.fullmatch(normalized):
        raise ValueError(_t(request, "admin_persona_scene_template_invalid_name"))
    return normalized


def _save_persona_scene_template(
    request: web.Request,
    *,
    persona: str,
    scene_name: str,
    scene_prompt: str,
    scene_caption: str,
) -> None:
    normalized_scene = _validate_scene_name(request, scene_name)
    path = _persona_file_map(_runtime_workspace(request), persona)["st_manifest.json"]
    raw_manifest = _read_text(path)
    manifest = _parse_json_object_text(
        raw_manifest,
        object_required_message=_t(request, "admin_json_object_required"),
    )
    prompts = manifest.get("scene_prompts")
    if not isinstance(prompts, dict):
        prompts = {}
    captions = manifest.get("scene_captions")
    if not isinstance(captions, dict):
        captions = {}

    prompt_value = scene_prompt.strip()
    caption_value = scene_caption.strip()
    if prompt_value:
        prompts[normalized_scene] = prompt_value
    else:
        prompts.pop(normalized_scene, None)
    if caption_value:
        captions[normalized_scene] = caption_value
    else:
        captions.pop(normalized_scene, None)

    if prompts:
        manifest["scene_prompts"] = prompts
    else:
        manifest.pop("scene_prompts", None)
    if captions:
        manifest["scene_captions"] = captions
    else:
        manifest.pop("scene_captions", None)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_pretty_json(manifest) + "\n" if manifest else "", encoding="utf-8")


async def _generate_persona_scene_preview(
    request: web.Request,
    *,
    persona: str,
    scene_name: str,
    scene_brief: str,
) -> PersonaScenePreview:
    config = _load_current_config(request)
    image_cfg = config.tools.image_gen
    if not image_cfg.enabled:
        raise ValueError(_t(request, "scene_tool_disabled"))

    workspace = _runtime_workspace(request)
    spec = build_scene_generation_spec(
        workspace,
        persona=persona,
        subcommand=scene_name,
        brief=scene_brief or None,
        image_gen_default_reference=image_cfg.reference_image,
    )
    if spec is None:
        items = "\n".join(f"- {name}" for name in available_scene_names(workspace, persona))
        raise ValueError(_t(request, "scene_unknown", name=scene_name, items=items))

    tool = ImageGenTool(
        workspace=workspace,
        api_key=image_cfg.api_key,
        base_url=image_cfg.base_url,
        model=image_cfg.model,
        proxy=image_cfg.proxy,
        timeout=image_cfg.timeout,
        reference_image=image_cfg.reference_image,
        restrict_to_workspace=config.tools.restrict_to_workspace,
    )
    tool.set_persona(persona)
    params: dict[str, str] = {"prompt": spec.prompt}
    if spec.reference_selector:
        params["reference_image"] = spec.reference_selector
    result = await tool.execute(**params)
    output_path = Path(extract_generated_path(result) or "")
    if not output_path.exists():
        raise ValueError(result if isinstance(result, str) else _t(request, "generic_error"))
    return PersonaScenePreview(
        scene_name=scene_name,
        brief=scene_brief,
        caption=render_scene_caption(
            _admin_language(request),
            persona_label=persona
            if persona != DEFAULT_PERSONA
            else _t(request, "scene_persona_fallback"),
            spec=spec,
        ),
        image_path=output_path,
        image_data_url=_scene_preview_image_data_url(output_path),
    )


def _write_text_file(path: Path, content: str, *, optional: bool = False) -> None:
    if optional and not content.strip():
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n" if content else "", encoding="utf-8")


def _normalize_markdown_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().strip("#")).strip().lower()


def _default_user_template_text() -> str:
    try:
        return (pkg_files("hahobot") / "templates" / "USER.md").read_text(encoding="utf-8")
    except Exception:
        return "# Relationship\n"


def _split_markdown_level2_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return "", []

    lines = normalized.split("\n")
    preamble: list[str] = []
    sections: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            if current_heading is None:
                current_heading = line
            else:
                sections.append((current_heading, "\n".join(current_lines).strip()))
                current_heading = line
            current_lines = []
            continue

        if current_heading is None:
            preamble.append(line)
        else:
            current_lines.append(line)

    if current_heading is not None:
        sections.append((current_heading, "\n".join(current_lines).strip()))

    return "\n".join(preamble).strip(), sections


def _join_markdown_block(heading: str, body: str) -> str:
    body = body.strip()
    return f"{heading}\n\n{body}".strip() if body else heading.strip()


def _append_markdown_blocks(existing: str, blocks: list[str]) -> str:
    merged = existing.strip()
    for block in blocks:
        candidate = block.strip()
        if not candidate:
            continue
        if candidate in merged:
            continue
        merged = f"{merged}\n\n{candidate}".strip() if merged else candidate
    return merged


def _section_body(block: str) -> str:
    lines = block.strip().splitlines()
    if len(lines) >= 3 and lines[0].startswith("## "):
        return "\n".join(lines[2:]).strip()
    return block.strip()


def _classify_legacy_user_sections(
    user_text: str,
) -> tuple[str, list[str], list[str], list[str], bool]:
    preamble, sections = _split_markdown_level2_sections(user_text)
    if not sections:
        return user_text, [], [], [], False

    moved_profile: list[str] = []
    moved_insights: list[str] = []
    kept_sections: list[str] = []

    for heading, body in sections:
        title = _normalize_markdown_title(heading[3:])
        block = _join_markdown_block(heading, body)
        if title in _LEGACY_USER_PROFILE_SECTION_TITLES:
            moved_profile.append(block)
        elif title in _LEGACY_USER_INSIGHTS_SECTION_TITLES:
            moved_insights.append(block)
        else:
            kept_sections.append(block)

    preamble = preamble.strip()
    is_legacy_profile_shell = bool(
        preamble and _LEGACY_USER_PROFILE_TITLE_RE.match(preamble.splitlines()[0].strip())
    )
    return preamble, moved_profile, moved_insights, kept_sections, is_legacy_profile_shell


def _legacy_user_migration_preview(
    user_text: str,
    profile_text: str,
    insights_text: str,
) -> dict[str, Any] | None:
    migrated_user, migrated_profile, migrated_insights, moved_profile, moved_insights = (
        _migrate_legacy_user_sections(user_text, profile_text, insights_text)
    )
    if moved_profile == 0 and moved_insights == 0:
        return None

    result_files: list[dict[str, str]] = []
    if migrated_user.strip() != user_text.strip():
        result_files.append({"name": "USER.md", "content": migrated_user})
    if migrated_profile.strip() != profile_text.strip():
        result_files.append({"name": "PROFILE.md", "content": migrated_profile})
    if migrated_insights.strip() != insights_text.strip():
        result_files.append({"name": "INSIGHTS.md", "content": migrated_insights})

    return {
        "profile_count": moved_profile,
        "insights_count": moved_insights,
        "result_files": result_files,
    }


def _migrate_legacy_user_sections(
    user_text: str,
    profile_text: str,
    insights_text: str,
) -> tuple[str, str, str, int, int]:
    preamble, moved_profile, moved_insights, kept_sections, is_legacy_profile_shell = (
        _classify_legacy_user_sections(user_text)
    )
    if not moved_profile and not moved_insights:
        return user_text, profile_text, insights_text, 0, 0

    migrated_profile = _append_markdown_blocks(profile_text, moved_profile)
    migrated_insights = _append_markdown_blocks(insights_text, moved_insights)

    if is_legacy_profile_shell:
        user_parts = ["# Relationship"]
        if kept_sections:
            for block in kept_sections:
                title = _normalize_markdown_title(block.splitlines()[0][3:])
                user_parts.append(
                    _section_body(block)
                    if title in _LEGACY_USER_RELATIONSHIP_SECTION_TITLES
                    else block
                )
            migrated_user = "\n\n".join(part for part in user_parts if part.strip())
        else:
            migrated_user = _default_user_template_text().strip()
    else:
        parts = [preamble] if preamble else []
        parts.extend(kept_sections)
        migrated_user = "\n\n".join(part for part in parts if part.strip()).strip()
        if not migrated_user:
            migrated_user = _default_user_template_text().strip()

    return (
        migrated_user,
        migrated_profile,
        migrated_insights,
        len(moved_profile),
        len(moved_insights),
    )


def _write_json_file(
    path: Path,
    raw: str,
    *,
    object_required_message: str,
    optional: bool = True,
) -> None:
    if optional and not raw.strip():
        if path.exists():
            path.unlink()
        return
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(object_required_message)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_pretty_json(data) + "\n", encoding="utf-8")


_PERSONA_MANIFEST_KNOWN_KEYS = (
    "reference_image",
    "referenceImage",
    "reference_images",
    "referenceImages",
    "scene_prompts",
    "scenePrompts",
    "scene_captions",
    "sceneCaptions",
    "response_filter_tags",
    "responseFilterTags",
)


def _parse_json_object_text(raw: str, *, object_required_message: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(object_required_message)
    return data


def _manifest_map_to_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    items: list[str] = []
    for key in sorted(value):
        raw_value = value.get(key)
        if not isinstance(key, str) or not isinstance(raw_value, str):
            continue
        cleaned_key = key.strip()
        cleaned_value = raw_value.strip()
        if cleaned_key and cleaned_value:
            items.append(f"{cleaned_key} = {cleaned_value}")
    return "\n".join(items)


def _manifest_tags_to_text(value: Any) -> str:
    return ", ".join(normalize_response_filter_tags(value))


def _manifest_map_rows(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key in sorted(value):
            raw_value = value.get(key)
            if not isinstance(key, str) or not isinstance(raw_value, str):
                continue
            rows.append({"key": key.strip(), "value": raw_value.strip()})
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "key": str(item.get("key", "") or "").strip(),
                    "value": str(item.get("value", "") or "").strip(),
                }
            )
    elif isinstance(value, str):
        rows = [
            {"key": key, "value": mapped}
            for key, mapped in _parse_manifest_mapping_lines(
                value,
                invalid_message="invalid row: {line}",
            ).items()
        ]
    return rows or [{"key": "", "value": ""}]


def _persona_manifest_form_values(raw_manifest: str) -> dict[str, Any]:
    try:
        data = _parse_json_object_text(
            raw_manifest,
            object_required_message="manifest must be object",
        )
    except Exception:
        data = {}

    return {
        "response_filter_tags": _manifest_tags_to_text(
            data.get("response_filter_tags", data.get("responseFilterTags"))
        ),
        "reference_image": str(
            data.get("reference_image", data.get("referenceImage")) or ""
        ).strip(),
        "reference_images_rows": _manifest_map_rows(
            data.get("reference_images", data.get("referenceImages"))
        ),
        "scene_prompts_rows": _manifest_map_rows(
            data.get("scene_prompts", data.get("scenePrompts"))
        ),
        "scene_captions_rows": _manifest_map_rows(
            data.get("scene_captions", data.get("sceneCaptions"))
        ),
    }


def _parse_manifest_mapping_lines(raw: str, *, invalid_message: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_no, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(invalid_message.format(line=line_no))
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(invalid_message.format(line=line_no))
        result[key] = value
    return result


def _parse_manifest_mapping_rows(rows: Any, *, invalid_message: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(rows, list):
        return result
    for index, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "") or "").strip()
        value = str(item.get("value", "") or "").strip()
        if not key and not value:
            continue
        if not key or not value:
            raise ValueError(invalid_message.format(line=index))
        result[key] = value
    return result


def _manifest_rows_from_form(form: Any, field_name: str) -> list[dict[str, str]]:
    keys = [str(value) for value in form.getall(f"{field_name}_key", [])]
    values = [str(value) for value in form.getall(f"{field_name}_value", [])]
    if not keys and not values and field_name in form:
        legacy_value = str(form.get(field_name, ""))
        try:
            return _manifest_map_rows(legacy_value)
        except Exception:
            return (
                [{"key": legacy_value.strip(), "value": ""}]
                if legacy_value.strip()
                else [{"key": "", "value": ""}]
            )
    row_count = max(len(keys), len(values), 1)
    return [
        {
            "key": keys[index] if index < len(keys) else "",
            "value": values[index] if index < len(values) else "",
        }
        for index in range(row_count)
    ]


def _merge_persona_manifest_form(
    *,
    raw_manifest: str,
    form_values: dict[str, str],
    object_required_message: str,
    mapping_invalid_message: str,
) -> str:
    data = _parse_json_object_text(
        raw_manifest,
        object_required_message=object_required_message,
    )
    merged = dict(data)
    for key in _PERSONA_MANIFEST_KNOWN_KEYS:
        merged.pop(key, None)

    tags = normalize_response_filter_tags(form_values.get("response_filter_tags"))
    if tags:
        merged["response_filter_tags"] = list(tags)

    reference_image = form_values.get("reference_image", "").strip()
    if reference_image:
        merged["reference_image"] = reference_image

    reference_images = _parse_manifest_mapping_rows(
        form_values.get("reference_images_rows", []),
        invalid_message=mapping_invalid_message,
    )
    if reference_images:
        merged["reference_images"] = reference_images

    scene_prompts = _parse_manifest_mapping_rows(
        form_values.get("scene_prompts_rows", []),
        invalid_message=mapping_invalid_message,
    )
    if scene_prompts:
        merged["scene_prompts"] = scene_prompts

    scene_captions = _parse_manifest_mapping_rows(
        form_values.get("scene_captions_rows", []),
        invalid_message=mapping_invalid_message,
    )
    if scene_captions:
        merged["scene_captions"] = scene_captions

    return _pretty_json(merged) if merged else ""


def _persona_file_map(workspace: Path, persona: str) -> dict[str, Path]:
    root = persona_workspace(workspace, persona)
    return {
        "SOUL.md": root / "SOUL.md",
        "USER.md": root / "USER.md",
        "PROFILE.md": root / "PROFILE.md",
        "INSIGHTS.md": root / "INSIGHTS.md",
        "STYLE.md": root / "STYLE.md",
        "LORE.md": root / "LORE.md",
        "VOICE.json": root / PERSONA_VOICE_FILENAME,
        "st_manifest.json": root / PERSONA_METADATA_DIRNAME / PERSONA_ST_MANIFEST_FILENAME,
    }


def _ensure_persona_scaffold(workspace: Path, persona: str) -> Path:
    root = workspace if persona == DEFAULT_PERSONA else personas_root(workspace) / persona
    ensure_dir(root)
    ensure_dir(root / "memory")
    ensure_dir(root / PERSONA_METADATA_DIRNAME)
    for filename in ("SOUL.md", "USER.md"):
        target = root / filename
        if not target.exists():
            target.write_text("", encoding="utf-8")
    for filename in ("MEMORY.md", "HISTORY.md"):
        target = root / "memory" / filename
        if not target.exists():
            target.write_text("", encoding="utf-8")
    return root


def _render_personas_page(
    request: web.Request,
    *,
    flash: str | None = None,
    error: str | None = None,
) -> web.Response:
    workspace = _runtime_workspace(request)
    items = []
    for persona in list_personas(workspace):
        label = (
            _t(request, "admin_default_persona_label") if persona == DEFAULT_PERSONA else persona
        )
        items.append(
            f'<li><a href="/admin/personas/{escape(persona)}"><strong>{escape(label)}</strong>'
            f'<span class="muted">{escape(str(persona_workspace(workspace, persona)))}</span></a></li>'
        )

    return _page(
        template_name="gateway/admin/personas.html",
        title=_t(request, "admin_personas_title"),
        heading=_t(request, "admin_personas_heading"),
        request=request,
        flash=flash,
        error=error,
        personas_nav_label=_t(request, "admin_nav_personas"),
        personas_desc_html=_markup(_th(request, "admin_card_personas_desc")),
        workspace_label=_t(request, "admin_meta_workspace"),
        workspace_path=str(workspace),
        create_persona_title=_t(request, "admin_card_create_persona"),
        create_persona_desc_html=_markup(_th(request, "admin_card_create_persona_desc")),
        persona_name_label=_t(request, "admin_persona_name_label"),
        create_persona_button=_t(request, "admin_button_create_persona"),
        personas_title=_t(request, "admin_card_personas"),
        persona_items_html=_markup("".join(items)),
    )


async def _admin_personas_page(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    flash = request.query.get("saved")
    if flash == "created":
        flash = _t(request, "admin_persona_created")
    elif flash == "updated":
        flash = _t(request, "admin_persona_updated")
    else:
        flash = None
    return _render_personas_page(request, flash=flash)


async def _admin_persona_create(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    form = await request.post()
    raw_name = str(form.get("name", "")).strip()
    normalized = normalize_persona_name(raw_name)
    if not normalized or normalized == DEFAULT_PERSONA:
        return _render_personas_page(
            request,
            error=_t(request, "admin_error_invalid_persona_name"),
        )

    root = personas_root(_runtime_workspace(request))
    persona_dir = root / normalized
    if persona_dir.exists():
        raise _redirect(request, f"/admin/personas/{quote(normalized, safe='')}")

    _ensure_persona_scaffold(_runtime_workspace(request), normalized)
    raise _redirect(request, f"/admin/personas/{quote(normalized, safe='')}?saved=created")


def _resolved_persona_or_404(request: web.Request) -> str:
    requested = request.match_info["persona"]
    workspace = _runtime_workspace(request)
    if requested == DEFAULT_PERSONA:
        return DEFAULT_PERSONA
    resolved = resolve_persona_name(workspace, requested)
    if resolved is None:
        raise web.HTTPNotFound()
    return resolved


def _render_persona_detail_page(
    request: web.Request,
    *,
    persona: str,
    values: dict[str, str],
    manifest_form_values: dict[str, Any] | None = None,
    scene_preview_form: dict[str, str] | None = None,
    scene_preview: PersonaScenePreview | None = None,
    scene_preview_error: str | None = None,
    scene_template_form: dict[str, str] | None = None,
    scene_template_error: str | None = None,
    migration_preview: dict[str, Any] | None = None,
    flash: str | None = None,
    error: str | None = None,
) -> web.Response:
    persona_root = persona_workspace(_runtime_workspace(request), persona)
    scene_values = manifest_form_values or _persona_manifest_form_values(values["st_manifest.json"])
    preview_form = scene_preview_form or _scene_preview_form_defaults(request, persona)
    show_template_form = scene_preview is not None or scene_template_error is not None
    template_form = scene_template_form or _scene_template_form_defaults(
        request,
        persona,
        preview=scene_preview,
        preview_form=preview_form,
    )

    def _editor_card(title: str, desc_key: str, field_name: str, value: str) -> str:
        return (
            '<label class="card stack editor-card">'
            f"<strong>{escape(title)}</strong>"
            f'<div class="muted">{_th(request, desc_key)}</div>'
            f'<textarea name="{escape(field_name)}" spellcheck="false">{escape(value)}</textarea>'
            "</label>"
        )

    def _scene_input(label_key: str, hint_key: str, field_name: str, value: str) -> str:
        return (
            '<label class="stack">'
            f"<strong>{escape(_t(request, label_key))}</strong>"
            f'<div class="muted">{_th(request, hint_key)}</div>'
            f'<input type="text" name="{escape(field_name)}" value="{escape(value)}" spellcheck="false">'
            "</label>"
        )

    def _scene_textarea(
        label_key: str,
        hint_key: str,
        field_name: str,
        value: str,
        *,
        rows: int = 5,
    ) -> str:
        return (
            '<label class="stack">'
            f"<strong>{escape(_t(request, label_key))}</strong>"
            f'<div class="muted">{_th(request, hint_key)}</div>'
            f'<textarea name="{escape(field_name)}" rows="{rows}" spellcheck="false">{escape(value)}</textarea>'
            "</label>"
        )

    def _scene_map_row(field_name: str, row: dict[str, str]) -> str:
        return (
            '<div class="scene-map-row" data-scene-map-row>'
            f'<input type="text" name="{escape(field_name)}_key" value="{escape(row.get("key", ""))}" spellcheck="false">'
            f'<input type="text" name="{escape(field_name)}_value" value="{escape(row.get("value", ""))}" spellcheck="false">'
            '<div class="scene-map-row-actions">'
            f'<button type="button" class="ghost" data-scene-map-move-up>{escape(_t(request, "admin_provider_pool_move_up"))}</button>'
            f'<button type="button" class="ghost" data-scene-map-move-down>{escape(_t(request, "admin_provider_pool_move_down"))}</button>'
            f'<button type="button" class="ghost" data-scene-map-remove>{escape(_t(request, "admin_provider_pool_remove"))}</button>'
            "</div>"
            "</div>"
        )

    def _scene_map_editor(
        title_key: str,
        hint_key: str,
        field_name: str,
        rows: list[dict[str, str]],
    ) -> str:
        rows_html = "".join(_scene_map_row(field_name, row) for row in rows)
        template_row = _scene_map_row(field_name, {"key": "", "value": ""})
        return f"""
          <div class="stack">
            <strong>{escape(_t(request, title_key))}</strong>
            <div class="muted">{_th(request, hint_key)}</div>
            <div class="scene-map-editor" data-scene-map-editor>
              <div class="scene-map-head">
                <span>{escape(_t(request, "admin_persona_scene_column_name"))}</span>
                <span>{escape(_t(request, "admin_persona_scene_column_value"))}</span>
                <span>{escape(_t(request, "admin_provider_pool_column_actions"))}</span>
              </div>
              <div class="scene-map-rows" data-scene-map-rows>
                {rows_html}
              </div>
              <template data-scene-map-template>{template_row}</template>
              <div class="actions provider-pool-actions">
                <button type="button" class="ghost" data-scene-map-add>
                  {escape(_t(request, "admin_persona_scene_add_row"))}
                </button>
              </div>
            </div>
          </div>
        """

    def _scene_editor_card() -> str:
        return f"""
          <section class="card stack editor-card">
            <strong>{escape(_t(request, "admin_persona_scene_title"))}</strong>
            <div class="muted">{_th(request, "admin_persona_scene_desc")}</div>
            {
            _scene_input(
                "admin_persona_scene_reference_label",
                "admin_persona_scene_reference_hint",
                "manifest_reference_image",
                scene_values["reference_image"],
            )
        }
            {
            _scene_map_editor(
                "admin_persona_scene_references_label",
                "admin_persona_scene_references_hint",
                "manifest_reference_images",
                scene_values["reference_images_rows"],
            )
        }
            {
            _scene_map_editor(
                "admin_persona_scene_prompts_label",
                "admin_persona_scene_prompts_hint",
                "manifest_scene_prompts",
                scene_values["scene_prompts_rows"],
            )
        }
            {
            _scene_map_editor(
                "admin_persona_scene_captions_label",
                "admin_persona_scene_captions_hint",
                "manifest_scene_captions",
                scene_values["scene_captions_rows"],
            )
        }
            {
            _scene_input(
                "admin_persona_scene_tags_label",
                "admin_persona_scene_tags_hint",
                "manifest_response_filter_tags",
                scene_values["response_filter_tags"],
            )
        }
          </section>
        """

    def _scene_template_form_html() -> str:
        return f"""
          <form method="post" action="/admin/personas/{escape(persona)}/scene-template-save" class="stack">
            <input type="hidden" name="preview_scene_name" value="{escape(preview_form.get("scene_name", ""))}">
            <input type="hidden" name="preview_scene_brief" value="{escape(preview_form.get("scene_brief", ""))}">
            <div class="field-grid">
              <label class="field">
                <span class="label">{escape(_t(request, "admin_persona_scene_template_name_label"))}</span>
                <input type="text" name="scene_name" value="{escape(template_form.get("scene_name", ""))}" spellcheck="false">
              </label>
              <label class="field full">
                <span class="label">{escape(_t(request, "admin_persona_scene_template_prompt_label"))}</span>
                <textarea name="scene_prompt" rows="4" spellcheck="false">{escape(template_form.get("scene_prompt", ""))}</textarea>
              </label>
              <label class="field full">
                <span class="label">{escape(_t(request, "admin_persona_scene_template_caption_label"))}</span>
                <textarea name="scene_caption" rows="3" spellcheck="false">{escape(template_form.get("scene_caption", ""))}</textarea>
              </label>
            </div>
            <div class="actions">
              <button type="submit" class="ghost">{escape(_t(request, "admin_persona_scene_template_save"))}</button>
            </div>
          </form>
        """

    def _scene_preview_card() -> str:
        available = available_scene_names(_runtime_workspace(request), persona)
        selected_scene = preview_form.get("scene_name", "")
        option_names = [*available, "generate"]
        if selected_scene and selected_scene not in option_names:
            option_names.append(selected_scene)
        options_html = "".join(
            f'<option value="{escape(name)}"{" selected" if name == selected_scene else ""}>{escape(name)}</option>'
            for name in option_names
        )
        result_html = ""
        if scene_preview_error:
            result_html = f'<div class="notice error">{escape(scene_preview_error)}</div>'
        elif scene_preview is not None:
            image_html = (
                f'<div class="qr-preview"><img src="{escape(scene_preview.image_data_url)}" alt="{escape(scene_preview.caption)}"></div>'
                if scene_preview.image_data_url
                else ""
            )
            template_notice = (
                f'<div class="notice error">{escape(scene_template_error)}</div>'
                if scene_template_error
                else ""
            )
            template_form_html = _scene_template_form_html() if show_template_form else ""
            result_html = f"""
              <div class="stack">
                <div class="muted"><strong>{escape(_t(request, "admin_persona_scene_preview_caption_label"))}</strong>: {escape(scene_preview.caption)}</div>
                <div class="muted"><strong>{escape(_t(request, "admin_persona_scene_preview_path_label"))}</strong>: <code>{escape(str(scene_preview.image_path))}</code></div>
                {image_html}
                {template_notice}
                {template_form_html}
              </div>
            """
        elif show_template_form:
            template_notice = (
                f'<div class="notice error">{escape(scene_template_error)}</div>'
                if scene_template_error
                else ""
            )
            result_html = f"""
              <div class="stack">
                {template_notice}
                {_scene_template_form_html()}
              </div>
            """
        return f"""
          <section class="card stack">
            <div class="section-head">
              <h2>{escape(_t(request, "admin_persona_scene_preview_title"))}</h2>
              <div class="muted">{_th(request, "admin_persona_scene_preview_desc")}</div>
            </div>
            <form method="post" action="/admin/personas/{escape(persona)}/scene-preview" class="stack">
              <div class="field-grid">
                <label class="field">
                  <span class="label">{escape(_t(request, "admin_persona_scene_preview_scene_label"))}</span>
                  <select name="scene_name">{options_html}</select>
                </label>
                <label class="field full">
                  <span class="label">{escape(_t(request, "admin_persona_scene_preview_brief_label"))}</span>
                  <input type="text" name="scene_brief" value="{escape(preview_form.get("scene_brief", ""))}" spellcheck="false">
                </label>
              </div>
              <div class="actions">
                <button type="submit">{escape(_t(request, "admin_persona_scene_preview_generate"))}</button>
              </div>
            </form>
            {result_html}
          </section>
        """

    def _memory_metadata_card(title: str, value: str) -> str:
        summary = summarize_memory_metadata(value)
        empty_hint = (
            f'<div class="muted">{_th(request, "admin_persona_memory_metadata_empty")}</div>'
            if summary.tagged_bullets == 0 and summary.legacy_verify_markers == 0
            else ""
        )
        return f"""
          <section class="card stack">
            <strong>{escape(title)}</strong>
            <div class="stat-grid">
              <div class="stat-card">
                <span>{escape(_t(request, "admin_persona_memory_metadata_tagged"))}</span>
                <strong>{summary.tagged_bullets}</strong>
              </div>
              <div class="stat-card">
                <span>{escape(_t(request, "admin_persona_memory_metadata_last_verified"))}</span>
                <strong>{summary.with_last_verified}</strong>
              </div>
              <div class="stat-card">
                <span>{escape(_t(request, "admin_persona_memory_metadata_legacy_verify"))}</span>
                <strong>{summary.legacy_verify_markers}</strong>
              </div>
              <div class="stat-card">
                <span>{escape(_t(request, "admin_persona_memory_metadata_confidence_high"))}</span>
                <strong>{summary.high_confidence}</strong>
              </div>
              <div class="stat-card">
                <span>{escape(_t(request, "admin_persona_memory_metadata_confidence_medium"))}</span>
                <strong>{summary.medium_confidence}</strong>
              </div>
              <div class="stat-card">
                <span>{escape(_t(request, "admin_persona_memory_metadata_confidence_low"))}</span>
                <strong>{summary.low_confidence}</strong>
              </div>
            </div>
            {empty_hint}
          </section>
        """

    preview_card = ""
    if migration_preview:
        result_title_keys = {
            "USER.md": "admin_persona_migration_user_result_title",
            "PROFILE.md": "admin_persona_migration_profile_result_title",
            "INSIGHTS.md": "admin_persona_migration_insights_result_title",
        }
        result_sections = "".join(
            f"""
              <div class="stack">
                <div><strong>{escape(_t(request, result_title_keys[result["name"]]))}</strong></div>
                <pre class="code-block"><code>{escape(result["content"])}</code></pre>
              </div>
            """
            for result in migration_preview["result_files"]
        )
        preview_card = f"""
          <section class="card stack">
            <div class="section-head">
              <h2>{escape(_t(request, "admin_persona_migration_preview_title"))}</h2>
              <div class="muted">{_th(request, "admin_persona_migration_preview_desc")}</div>
            </div>
            <div class="stat-grid">
              <div class="stat-card">
                <span>{escape(_t(request, "admin_persona_migration_profile_count_label"))}</span>
                <strong>{migration_preview["profile_count"]}</strong>
              </div>
              <div class="stat-card">
                <span>{escape(_t(request, "admin_persona_migration_insights_count_label"))}</span>
                <strong>{migration_preview["insights_count"]}</strong>
              </div>
            </div>
            <div class="stack">
              <div><strong>{escape(_t(request, "admin_persona_migration_result_title"))}</strong></div>
              {result_sections}
            </div>
            <div class="actions">
              <form method="post" action="/admin/personas/{escape(persona)}/migrate-user" class="inline-form">
                <button type="submit" class="ghost">{escape(_t(request, "admin_button_migrate_persona_user"))}</button>
              </form>
            </div>
          </section>
        """
    else:
        preview_card = f"""
          <section class="card stack">
            <div class="section-head">
              <h2>{escape(_t(request, "admin_persona_migration_preview_title"))}</h2>
              <div class="muted">{_th(request, "admin_persona_migration_none_desc")}</div>
            </div>
          </section>
        """

    metadata_card = f"""
      <section class="card stack">
        <div class="section-head">
          <h2>{escape(_t(request, "admin_persona_memory_metadata_title"))}</h2>
          <div class="muted">{_th(request, "admin_persona_memory_metadata_desc")}</div>
        </div>
        <div class="editor-grid">
          {_memory_metadata_card(_t(request, "admin_persona_memory_metadata_profile_title"), values["PROFILE.md"])}
          {_memory_metadata_card(_t(request, "admin_persona_memory_metadata_insights_title"), values["INSIGHTS.md"])}
        </div>
        <div class="muted">{_th(request, "admin_persona_memory_metadata_example_desc")}</div>
        <pre class="code-block"><code>- Prefers short review loops. &lt;!-- hahobot-meta: confidence=high last_verified=2026-04-08 --&gt;</code></pre>
      </section>
    """

    return _page(
        template_name="gateway/admin/persona_detail.html",
        title=_t(request, "admin_persona_title", persona=persona),
        heading=_t(request, "admin_persona_heading", persona=persona),
        request=request,
        flash=flash,
        error=error,
        personas_nav_label=_t(request, "admin_nav_personas"),
        persona_name=persona,
        persona_label=_t(request, "admin_persona_label"),
        directory_label=_t(request, "admin_persona_directory_label"),
        persona_root=str(persona_root),
        save_persona_label=_t(request, "admin_button_save_persona"),
        persona_intro_html=_markup(_th(request, "admin_persona_intro")),
        optional_hint_html=_markup(_th(request, "admin_persona_optional_hint")),
        migrate_desc_html=_markup(_th(request, "admin_persona_migrate_desc")),
        preview_card_html=_markup(preview_card),
        scene_preview_card_html=_markup(_scene_preview_card()),
        metadata_card_html=_markup(metadata_card),
        primary_editor_cards_html=_markup(
            _editor_card("SOUL.md", "admin_persona_soul_desc", "soul_md", values["SOUL.md"])
            + _editor_card("USER.md", "admin_persona_user_desc", "user_md", values["USER.md"])
        ),
        secondary_editor_cards_html=_markup(
            _editor_card(
                "PROFILE.md",
                "admin_persona_profile_desc",
                "profile_md",
                values["PROFILE.md"],
            )
            + _editor_card(
                "INSIGHTS.md",
                "admin_persona_insights_desc",
                "insights_md",
                values["INSIGHTS.md"],
            )
            + _editor_card("STYLE.md", "admin_persona_style_desc", "style_md", values["STYLE.md"])
        ),
        tertiary_editor_cards_html=_markup(
            _editor_card("LORE.md", "admin_persona_lore_desc", "lore_md", values["LORE.md"])
            + _editor_card(
                "VOICE.json", "admin_persona_voice_desc", "voice_json", values["VOICE.json"]
            )
            + _scene_editor_card()
        ),
        manifest_editor_cards_html=_markup(
            _editor_card(
                "st_manifest.json",
                "admin_persona_manifest_desc",
                "manifest_json",
                values["st_manifest.json"],
            )
        ),
    )


async def _admin_persona_page(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    persona = _resolved_persona_or_404(request)
    files = _persona_file_map(_runtime_workspace(request), persona)
    values = {
        "SOUL.md": _read_text(files["SOUL.md"]),
        "USER.md": _read_text(files["USER.md"]),
        "PROFILE.md": _read_text(files["PROFILE.md"]),
        "INSIGHTS.md": _read_text(files["INSIGHTS.md"]),
        "STYLE.md": _read_text(files["STYLE.md"]),
        "LORE.md": _read_text(files["LORE.md"]),
        "VOICE.json": _read_json_text(files["VOICE.json"]),
        "st_manifest.json": _read_json_text(files["st_manifest.json"]),
    }
    flash = None
    if request.query.get("saved") == "created":
        flash = _t(request, "admin_persona_created")
    elif request.query.get("saved") == "updated":
        flash = _t(request, "admin_persona_updated")
    elif request.query.get("scene_saved") == "1":
        flash = _t(request, "admin_persona_scene_saved", scene=request.query.get("scene", ""))
    elif request.query.get("migrated") == "1":
        flash = _t(
            request,
            "admin_persona_migrated",
            profile=request.query.get("profile", "0"),
            insights=request.query.get("insights", "0"),
        )
    elif request.query.get("migrated") == "none":
        flash = _t(request, "admin_persona_migrate_noop")
    return _render_persona_detail_page(
        request,
        persona=persona,
        values=values,
        migration_preview=_legacy_user_migration_preview(
            values["USER.md"],
            values["PROFILE.md"],
            values["INSIGHTS.md"],
        ),
        flash=flash,
    )


async def _admin_persona_scene_preview(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    persona = _resolved_persona_or_404(request)
    form = await request.post()
    files = _persona_file_map(_runtime_workspace(request), persona)
    values = {
        "SOUL.md": _read_text(files["SOUL.md"]),
        "USER.md": _read_text(files["USER.md"]),
        "PROFILE.md": _read_text(files["PROFILE.md"]),
        "INSIGHTS.md": _read_text(files["INSIGHTS.md"]),
        "STYLE.md": _read_text(files["STYLE.md"]),
        "LORE.md": _read_text(files["LORE.md"]),
        "VOICE.json": _read_json_text(files["VOICE.json"]),
        "st_manifest.json": _read_json_text(files["st_manifest.json"]),
    }
    preview_form = {
        "scene_name": str(form.get("scene_name", "")).strip() or "daily",
        "scene_brief": str(form.get("scene_brief", "")).strip(),
    }
    try:
        preview = await _generate_persona_scene_preview(
            request,
            persona=persona,
            scene_name=preview_form["scene_name"],
            scene_brief=preview_form["scene_brief"],
        )
    except Exception as exc:
        return _render_persona_detail_page(
            request,
            persona=persona,
            values=values,
            scene_preview_form=preview_form,
            scene_preview_error=str(exc),
            migration_preview=_legacy_user_migration_preview(
                values["USER.md"],
                values["PROFILE.md"],
                values["INSIGHTS.md"],
            ),
        )
    return _render_persona_detail_page(
        request,
        persona=persona,
        values=values,
        scene_preview_form=preview_form,
        scene_preview=preview,
        scene_template_form=_scene_template_form_defaults(
            request,
            persona,
            preview=preview,
            preview_form=preview_form,
        ),
        migration_preview=_legacy_user_migration_preview(
            values["USER.md"],
            values["PROFILE.md"],
            values["INSIGHTS.md"],
        ),
    )


async def _admin_persona_scene_template_save(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    persona = _resolved_persona_or_404(request)
    form = await request.post()
    files = _persona_file_map(_runtime_workspace(request), persona)
    values = {
        "SOUL.md": _read_text(files["SOUL.md"]),
        "USER.md": _read_text(files["USER.md"]),
        "PROFILE.md": _read_text(files["PROFILE.md"]),
        "INSIGHTS.md": _read_text(files["INSIGHTS.md"]),
        "STYLE.md": _read_text(files["STYLE.md"]),
        "LORE.md": _read_text(files["LORE.md"]),
        "VOICE.json": _read_json_text(files["VOICE.json"]),
        "st_manifest.json": _read_json_text(files["st_manifest.json"]),
    }
    template_form = {
        "scene_name": str(form.get("scene_name", "")).strip(),
        "scene_prompt": str(form.get("scene_prompt", "")),
        "scene_caption": str(form.get("scene_caption", "")),
    }
    preview_form = {
        "scene_name": str(form.get("preview_scene_name", "")).strip() or "daily",
        "scene_brief": str(form.get("preview_scene_brief", "")).strip()
        or template_form["scene_prompt"].strip(),
    }
    try:
        _save_persona_scene_template(
            request,
            persona=persona,
            scene_name=template_form["scene_name"],
            scene_prompt=template_form["scene_prompt"],
            scene_caption=template_form["scene_caption"],
        )
    except ValueError as exc:
        preview: PersonaScenePreview | None = None
        try:
            preview = await _generate_persona_scene_preview(
                request,
                persona=persona,
                scene_name=preview_form["scene_name"],
                scene_brief=preview_form["scene_brief"],
            )
        except Exception:
            preview = None
        return _render_persona_detail_page(
            request,
            persona=persona,
            values=values,
            scene_preview_form=preview_form,
            scene_preview=preview,
            scene_template_form=template_form,
            scene_template_error=_t(request, "admin_persona_scene_template_save_failed", error=exc),
            migration_preview=_legacy_user_migration_preview(
                values["USER.md"],
                values["PROFILE.md"],
                values["INSIGHTS.md"],
            ),
        )
    raise _redirect(
        request,
        f"/admin/personas/{quote(persona, safe='')}?scene_saved=1&scene={quote(template_form['scene_name'], safe='')}",
    )


async def _admin_persona_submit(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    persona = _resolved_persona_or_404(request)
    form = await request.post()
    files = _persona_file_map(_runtime_workspace(request), persona)
    manifest_form_values = {
        "response_filter_tags": str(form.get("manifest_response_filter_tags", "")),
        "reference_image": str(form.get("manifest_reference_image", "")),
        "reference_images_rows": _manifest_rows_from_form(form, "manifest_reference_images"),
        "scene_prompts_rows": _manifest_rows_from_form(form, "manifest_scene_prompts"),
        "scene_captions_rows": _manifest_rows_from_form(form, "manifest_scene_captions"),
    }
    values = {
        "SOUL.md": str(form.get("soul_md", "")),
        "USER.md": str(form.get("user_md", "")),
        "PROFILE.md": str(form.get("profile_md", "")),
        "INSIGHTS.md": str(form.get("insights_md", "")),
        "STYLE.md": str(form.get("style_md", "")),
        "LORE.md": str(form.get("lore_md", "")),
        "VOICE.json": str(form.get("voice_json", "")),
        "st_manifest.json": str(form.get("manifest_json", "")),
    }

    try:
        values["st_manifest.json"] = _merge_persona_manifest_form(
            raw_manifest=values["st_manifest.json"],
            form_values=manifest_form_values,
            object_required_message=_t(request, "admin_json_object_required"),
            mapping_invalid_message=_t(
                request,
                "admin_persona_scene_map_invalid",
                line="{line}",
            ),
        )
        _ensure_persona_scaffold(_runtime_workspace(request), persona)
        _write_text_file(files["SOUL.md"], values["SOUL.md"], optional=False)
        _write_text_file(files["USER.md"], values["USER.md"], optional=False)
        _write_text_file(files["PROFILE.md"], values["PROFILE.md"], optional=True)
        _write_text_file(files["INSIGHTS.md"], values["INSIGHTS.md"], optional=True)
        _write_text_file(files["STYLE.md"], values["STYLE.md"], optional=True)
        _write_text_file(files["LORE.md"], values["LORE.md"], optional=True)
        _write_json_file(
            files["VOICE.json"],
            values["VOICE.json"],
            optional=True,
            object_required_message=_t(request, "admin_json_object_required"),
        )
        _write_json_file(
            files["st_manifest.json"],
            values["st_manifest.json"],
            optional=True,
            object_required_message=_t(request, "admin_json_object_required"),
        )
    except Exception as exc:
        return _render_persona_detail_page(
            request,
            persona=persona,
            values=values,
            manifest_form_values=manifest_form_values,
            migration_preview=_legacy_user_migration_preview(
                values["USER.md"],
                values["PROFILE.md"],
                values["INSIGHTS.md"],
            ),
            error=str(exc),
        )

    raise _redirect(request, f"/admin/personas/{quote(persona, safe='')}?saved=updated")


async def _admin_persona_migrate_user(request: web.Request) -> web.Response:
    _require_admin_auth(request)
    persona = _resolved_persona_or_404(request)
    files = _persona_file_map(_runtime_workspace(request), persona)
    values = {
        "USER.md": _read_text(files["USER.md"]),
        "PROFILE.md": _read_text(files["PROFILE.md"]),
        "INSIGHTS.md": _read_text(files["INSIGHTS.md"]),
    }

    try:
        _ensure_persona_scaffold(_runtime_workspace(request), persona)
        user_md, profile_md, insights_md, moved_profile, moved_insights = (
            _migrate_legacy_user_sections(
                values["USER.md"],
                values["PROFILE.md"],
                values["INSIGHTS.md"],
            )
        )
        if moved_profile == 0 and moved_insights == 0:
            raise _redirect(request, f"/admin/personas/{quote(persona, safe='')}?migrated=none")

        _write_text_file(files["USER.md"], user_md, optional=False)
        _write_text_file(files["PROFILE.md"], profile_md, optional=True)
        _write_text_file(files["INSIGHTS.md"], insights_md, optional=True)
    except web.HTTPFound:
        raise
    except Exception as exc:
        page_values = {
            "SOUL.md": _read_text(files["SOUL.md"]),
            "USER.md": values["USER.md"],
            "PROFILE.md": values["PROFILE.md"],
            "INSIGHTS.md": values["INSIGHTS.md"],
            "STYLE.md": _read_text(files["STYLE.md"]),
            "LORE.md": _read_text(files["LORE.md"]),
            "VOICE.json": _read_json_text(files["VOICE.json"]),
            "st_manifest.json": _read_json_text(files["st_manifest.json"]),
        }
        return _render_persona_detail_page(
            request,
            persona=persona,
            values=page_values,
            migration_preview=_legacy_user_migration_preview(
                page_values["USER.md"],
                page_values["PROFILE.md"],
                page_values["INSIGHTS.md"],
            ),
            error=str(exc),
        )

    raise _redirect(
        request,
        f"/admin/personas/{quote(persona, safe='')}?migrated=1&profile={moved_profile}&insights={moved_insights}",
    )
