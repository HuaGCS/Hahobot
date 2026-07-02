"""Safety net: every curated-section config scalar must be visually editable.

The admin visual config editor (`hahobot/gateway/admin/field_specs.py`) is a
hand-maintained allowlist, so a newly added schema field can be silently missing
from the UI (this is exactly how `gateway.webui.*` was overlooked). This test
walks the pydantic `Config` schema for scalar leaf fields under the curated
sections and asserts each one is either covered by a `ConfigFieldSpec` or listed
in `_KNOWN_UNCOVERED` (advanced knobs intentionally left to the raw-JSON editor).

Adding a new scalar field under an audited section therefore fails this test
until it is either given a visual spec or explicitly acknowledged here — the list
can never silently rot, and nothing is ever silently un-editable.

Note: `channels.*` and `providers.*` are deliberately NOT audited — they are
rendered by bespoke channel/provider group widgets plus the raw-JSON fallback,
not one-spec-per-field.
"""

from __future__ import annotations

import enum
import types
import typing

from pydantic import BaseModel
from pydantic.alias_generators import to_camel

from hahobot.config.schema import Config
from hahobot.gateway.admin.field_specs import _CONFIG_FIELDS

# Top-level config sections whose scalar fields should each have a visual spec.
_AUDIT_ROOTS = {"agents", "gateway", "tools", "memory", "api", "a2a"}

_SCALARS = (bool, int, float, str)

# Scalar fields under audited sections deliberately left to the raw-JSON editor
# (advanced tuning / rarely changed). Promote one to a ConfigFieldSpec and remove
# it here when it deserves a visual field.
_KNOWN_UNCOVERED: frozenset[tuple[str, ...]] = frozenset(
    {
        ("agents", "defaults", "contextBlockLimit"),
        ("agents", "defaults", "maxToolResultChars"),
        ("agents", "defaults", "providerRetryMode"),
        ("agents", "defaults", "unifiedSession"),
        ("agents", "defaults", "sessionTtlMinutes"),
        ("agents", "defaults", "dream", "cron"),
        ("agents", "defaults", "dream", "intervalH"),
        ("agents", "defaults", "dream", "maxBatchSize"),
        ("agents", "defaults", "dream", "maxIterations"),
        ("agents", "defaults", "dream", "modelOverride"),
        ("memory", "archive", "indexBackend"),
        ("tools", "web", "enable"),
        ("tools", "web", "search", "timeout"),
    }
)


def _unwrap_optional(ann):
    """Strip ``Optional[X]`` / ``X | None`` down to ``X`` (both union spellings)."""
    origin = typing.get_origin(ann)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return ann


def _leaf_kind(ann) -> str | None:
    """Return a label if ``ann`` is an editable scalar/enum/Literal leaf, else None."""
    if typing.get_origin(ann) is typing.Literal:
        return "literal"
    if isinstance(ann, type):
        if issubclass(ann, enum.Enum):
            return "enum"
        if issubclass(ann, _SCALARS):
            return ann.__name__
    return None


def _walk(model: type[BaseModel], prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    leaves: list[tuple[str, ...]] = []
    for name, field in model.model_fields.items():
        path = prefix + (field.alias or to_camel(name),)
        ann = _unwrap_optional(field.annotation)
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            leaves += _walk(ann, path)
            continue
        origin = typing.get_origin(ann)
        if origin in (dict, list) or ann in (dict, list):
            continue  # containers → custom widgets / raw JSON
        if _leaf_kind(ann):
            leaves.append(path)
    return leaves


def _audited_scalar_paths() -> set[tuple[str, ...]]:
    return {p for p in _walk(Config) if p and p[0] in _AUDIT_ROOTS}


def test_curated_sections_have_visual_specs_or_are_acknowledged() -> None:
    covered = {spec.path for spec in _CONFIG_FIELDS}
    uncovered = _audited_scalar_paths() - covered

    missing = uncovered - _KNOWN_UNCOVERED  # new field with no spec + not acknowledged
    stale = _KNOWN_UNCOVERED - uncovered  # acknowledged but now covered/removed

    assert not missing, (
        "Config scalar fields under audited sections lack a visual ConfigFieldSpec. "
        "Add a spec in field_specs.py (with en/zh label+tooltip), or add the path to "
        "_KNOWN_UNCOVERED if it should stay raw-JSON-only:\n  "
        + "\n  ".join(".".join(p) for p in sorted(missing))
    )
    assert not stale, (
        "_KNOWN_UNCOVERED lists paths that are now covered (or no longer exist); "
        "remove them:\n  " + "\n  ".join(".".join(p) for p in sorted(stale))
    )


def test_known_uncovered_paths_exist_in_schema() -> None:
    """Guard against typos / renamed fields lingering in the exclusion set."""
    all_scalars = set(_walk(Config))
    orphans = _KNOWN_UNCOVERED - all_scalars
    assert not orphans, "stale _KNOWN_UNCOVERED entries not in schema:\n  " + "\n  ".join(
        ".".join(p) for p in sorted(orphans)
    )
