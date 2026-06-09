"""Integrity tests for the admin visual-config field specs.

Guards that every ConfigFieldSpec maps to a real config path and that every
i18n key referenced by the fields/sections exists in both locale files.
"""

from __future__ import annotations

import json
from pathlib import Path

from hahobot.config.schema import Config
from hahobot.gateway.admin.config_view import (
    _config_form_values,
    validate_admin_config_specs,
)
from hahobot.gateway.admin.field_specs import _CONFIG_FIELDS, _CONFIG_SECTIONS

_LOCALES = Path(__file__).resolve().parents[1] / "hahobot" / "locales"


def _resolve_path(dump: dict, path: tuple[str, ...]) -> bool:
    node = dump
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


def _load_texts(name: str) -> dict[str, str]:
    data = json.loads((_LOCALES / name).read_text(encoding="utf-8"))
    return data["texts"]


def test_api_and_a2a_field_paths_resolve_against_config_model() -> None:
    # Scope to the api/a2a fields added here. Some pre-existing fields point at
    # optional/dynamic config nodes (provider pool, memorix) that are absent
    # from a default dump, so a blanket check would have legitimate misses.
    dump = Config().model_dump(mode="json", by_alias=True)
    fields = [f for f in _CONFIG_FIELDS if f.path and f.path[0] in {"api", "a2a"}]
    assert fields, "expected api/a2a fields to be registered"
    unresolved = [f.name for f in fields if not _resolve_path(dump, f.path)]
    assert not unresolved, f"field paths not found in config model: {unresolved}"


def test_section_field_names_exist() -> None:
    known = {f.name for f in _CONFIG_FIELDS}
    for _title, _desc, field_names in _CONFIG_SECTIONS:
        missing = [n for n in field_names if n not in known]
        assert not missing, f"section references unknown fields: {missing}"


def test_field_names_unique() -> None:
    names = [f.name for f in _CONFIG_FIELDS]
    assert len(names) == len(set(names)), "duplicate field names in _CONFIG_FIELDS"


def test_all_i18n_keys_present_in_both_locales() -> None:
    en = _load_texts("en.json")
    zh = _load_texts("zh.json")

    required: set[str] = set()
    for field in _CONFIG_FIELDS:
        required.add(field.label_key)
        # The renderer always shows a tooltip derived from the label key.
        required.add(field.label_key.removesuffix("_label") + "_tooltip")
        if field.hint_key:
            required.add(field.hint_key)
    for title_key, desc_key, _names in _CONFIG_SECTIONS:
        required.add(title_key)
        required.add(desc_key)

    missing_en = sorted(k for k in required if k not in en)
    missing_zh = sorted(k for k in required if k not in zh)
    assert not missing_en, f"missing en.json keys: {missing_en}"
    assert not missing_zh, f"missing zh.json keys: {missing_zh}"


def test_field_i18n_values_are_format_safe() -> None:
    """Label/hint/tooltip strings are passed through str.format; literal braces
    must be escaped (``{{``/``}}``) or rendering raises KeyError."""
    for name in ("en.json", "zh.json"):
        texts = _load_texts(name)
        for field in _CONFIG_FIELDS:
            keys = [field.label_key, field.label_key.removesuffix("_label") + "_tooltip"]
            if field.hint_key:
                keys.append(field.hint_key)
            for key in keys:
                value = texts.get(key)
                if value is None:
                    continue
                # Must not raise: empty kwargs means any real placeholder would fail.
                value.format()


def test_config_form_values_cover_every_field() -> None:
    """Every field must get a render value (auto-derived or hand-written)."""
    values = _config_form_values(Config())
    missing = [f.name for f in _CONFIG_FIELDS if f.name not in values]
    assert not missing, f"fields with no render value: {missing}"


def test_auto_derive_matches_bool_kind() -> None:
    """Auto-derived bool fields stay real booleans (not stringified)."""
    values = _config_form_values(Config())
    for field in _CONFIG_FIELDS:
        if field.kind == "bool" and field.name in values:
            assert isinstance(values[field.name], bool), field.name


def test_validate_admin_config_specs_passes() -> None:
    # Must not raise for the shipped specs/locales (gateway startup guard).
    validate_admin_config_specs()


def test_serve_process_sections_note_restart() -> None:
    """API + A2A run in the serve process; their section descs must say so."""
    for lang, restart_word in (("en.json", "restart"), ("zh.json", "重启")):
        texts = _load_texts(lang)
        for section in ("api", "a2a"):
            desc = texts[f"admin_config_section_{section}_desc"]
            assert restart_word in desc and "serve" in desc, (lang, section)


def test_api_and_a2a_fields_registered() -> None:
    """The newly exposed api + a2a fields are present and sectioned."""
    names = {f.name for f in _CONFIG_FIELDS}
    expected = {
        "api_host",
        "api_port",
        "api_timeout",
        "a2a_enabled",
        "a2a_streaming",
        "a2a_name",
        "a2a_description",
        "a2a_version",
        "a2a_public_url",
        "a2a_timeout",
        "a2a_max_tasks",
    }
    assert expected <= names

    section_titles = {title for title, _desc, _names in _CONFIG_SECTIONS}
    assert "admin_config_section_api_title" in section_titles
    assert "admin_config_section_a2a_title" in section_titles
