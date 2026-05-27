"""Helpers for the Dream-proposed skill workflow.

Dream may use its filesystem tools to write candidate skills under
``<workspace>/skills/proposed/<slug>/SKILL.md``. Those proposals sit outside
the active skill discovery surface (``SkillsLoader`` walks ``skills/`` one
level deep, so ``proposed/`` itself is silently skipped because it has no
``SKILL.md``). An admin reviews each proposal and either approves it — which
moves the folder to ``skills/<slug>/`` and makes it discoverable on the next
``list_skills`` call — or rejects it, which deletes the proposal folder.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PROPOSED_DIRNAME = "proposed"
_SKILLS_DIRNAME = "skills"
_SKILL_FILENAME = "SKILL.md"
_DESCRIPTION_PREVIEW_CHARS = 240


@dataclass(frozen=True)
class ProposedSkill:
    """One Dream-proposed skill awaiting review."""

    name: str
    path: Path
    description: str
    body_preview: str


def proposed_skills_dir(workspace: Path) -> Path:
    """Return the directory where Dream stages proposed skills."""
    return workspace / _SKILLS_DIRNAME / _PROPOSED_DIRNAME


def active_skill_dir(workspace: Path, name: str) -> Path:
    """Return where an approved skill lives in the workspace."""
    return workspace / _SKILLS_DIRNAME / name


def _is_valid_skill_name(name: str) -> bool:
    return bool(_SKILL_NAME_RE.match(name))


def _extract_description(text: str) -> str:
    """Pull `description:` from YAML frontmatter, falling back to the first line."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            frontmatter = text[3:end]
            for line in frontmatter.splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                if key.strip().lower() == "description":
                    return value.strip().strip("\"'")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("---"):
            return stripped
    return ""


def _extract_body_preview(text: str) -> str:
    """Strip the YAML frontmatter and return a clipped body preview."""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            body = text[end + 4 :]
    cleaned = body.strip()
    if len(cleaned) <= _DESCRIPTION_PREVIEW_CHARS:
        return cleaned
    return cleaned[: _DESCRIPTION_PREVIEW_CHARS - 3].rstrip() + "..."


def list_proposed_skills(workspace: Path) -> list[ProposedSkill]:
    """List Dream-proposed skills in the workspace, sorted by name."""
    base = proposed_skills_dir(workspace)
    if not base.exists() or not base.is_dir():
        return []
    proposals: list[ProposedSkill] = []
    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir() or not _is_valid_skill_name(skill_dir.name):
            continue
        skill_file = skill_dir / _SKILL_FILENAME
        if not skill_file.is_file():
            continue
        try:
            text = skill_file.read_text(encoding="utf-8")
        except OSError:
            continue
        proposals.append(
            ProposedSkill(
                name=skill_dir.name,
                path=skill_file,
                description=_extract_description(text),
                body_preview=_extract_body_preview(text),
            )
        )
    return proposals


def approve_proposed_skill(workspace: Path, name: str) -> Path:
    """Promote ``proposed/<name>/`` to ``skills/<name>/`` and return the new path.

    Raises ``ValueError`` for an invalid name, a missing proposal, or a name
    collision with an already-active skill.
    """
    if not _is_valid_skill_name(name):
        raise ValueError(f"invalid skill name: {name!r}")
    source = proposed_skills_dir(workspace) / name
    if not source.is_dir() or not (source / _SKILL_FILENAME).is_file():
        raise ValueError(f"no proposed skill named {name!r}")
    target = active_skill_dir(workspace, name)
    if target.exists():
        raise ValueError(f"skill {name!r} already exists; rename the proposal first")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return target


def reject_proposed_skill(workspace: Path, name: str) -> Path:
    """Delete ``proposed/<name>/`` and return the path that was removed."""
    if not _is_valid_skill_name(name):
        raise ValueError(f"invalid skill name: {name!r}")
    source = proposed_skills_dir(workspace) / name
    if not source.exists():
        raise ValueError(f"no proposed skill named {name!r}")
    if not source.is_dir():
        raise ValueError(f"proposed skill path {source} is not a directory")
    shutil.rmtree(source)
    return source
