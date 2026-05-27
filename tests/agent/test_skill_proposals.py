"""Tests for the Dream-proposed skill review helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from hahobot.agent.skill_proposals import (
    active_skill_dir,
    approve_proposed_skill,
    list_proposed_skills,
    proposed_skills_dir,
    reject_proposed_skill,
)


def _seed_proposal(workspace: Path, name: str, body: str) -> Path:
    folder = proposed_skills_dir(workspace) / name
    folder.mkdir(parents=True, exist_ok=True)
    skill_file = folder / "SKILL.md"
    skill_file.write_text(body, encoding="utf-8")
    return skill_file


def test_list_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    assert list_proposed_skills(tmp_path) == []


def test_list_picks_up_proposed_skill_with_frontmatter(tmp_path: Path) -> None:
    body = (
        "---\n"
        "name: image-extract\n"
        "description: Extract text from images via vision + OCR pipeline\n"
        "---\n"
        "## Steps\n"
        "1. Call image_gen to caption the image.\n"
        "2. Pipe to OCR tool.\n"
    )
    _seed_proposal(tmp_path, "image-extract", body)
    proposals = list_proposed_skills(tmp_path)
    assert len(proposals) == 1
    assert proposals[0].name == "image-extract"
    assert proposals[0].description == "Extract text from images via vision + OCR pipeline"
    assert "## Steps" in proposals[0].body_preview


def test_list_skips_folder_without_skill_file(tmp_path: Path) -> None:
    (proposed_skills_dir(tmp_path) / "empty").mkdir(parents=True)
    assert list_proposed_skills(tmp_path) == []


def test_list_rejects_invalid_skill_names(tmp_path: Path) -> None:
    _seed_proposal(tmp_path, "ok-name", "---\nname: ok-name\n---\nbody")
    bad = proposed_skills_dir(tmp_path) / "../escape"
    bad.parent.mkdir(parents=True, exist_ok=True)
    # Symlink-style escape isn't possible without OS support; just confirm
    # underscored garbage like a leading dot is filtered.
    (proposed_skills_dir(tmp_path) / ".hidden").mkdir()
    proposals = list_proposed_skills(tmp_path)
    assert {p.name for p in proposals} == {"ok-name"}


def test_approve_moves_folder_into_active_skills(tmp_path: Path) -> None:
    _seed_proposal(tmp_path, "image-extract", "---\nname: image-extract\n---\nbody")
    target = approve_proposed_skill(tmp_path, "image-extract")
    assert target == active_skill_dir(tmp_path, "image-extract")
    assert (target / "SKILL.md").is_file()
    assert not (proposed_skills_dir(tmp_path) / "image-extract").exists()


def test_approve_refuses_on_name_collision(tmp_path: Path) -> None:
    _seed_proposal(tmp_path, "image-extract", "---\nname: image-extract\n---\nbody")
    active = active_skill_dir(tmp_path, "image-extract")
    active.mkdir(parents=True)
    (active / "SKILL.md").write_text("existing skill", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        approve_proposed_skill(tmp_path, "image-extract")
    # Original proposal remains untouched
    assert (proposed_skills_dir(tmp_path) / "image-extract" / "SKILL.md").is_file()


def test_approve_rejects_unknown_skill(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no proposed skill"):
        approve_proposed_skill(tmp_path, "nope")


def test_approve_rejects_invalid_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid skill name"):
        approve_proposed_skill(tmp_path, "../escape")


def test_reject_deletes_proposal_folder(tmp_path: Path) -> None:
    folder = _seed_proposal(tmp_path, "image-extract", "---\nname: image-extract\n---\nbody").parent
    removed = reject_proposed_skill(tmp_path, "image-extract")
    assert removed == folder
    assert not folder.exists()


def test_reject_rejects_unknown_skill(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no proposed skill"):
        reject_proposed_skill(tmp_path, "nope")


def test_reject_rejects_invalid_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid skill name"):
        reject_proposed_skill(tmp_path, "../escape")
