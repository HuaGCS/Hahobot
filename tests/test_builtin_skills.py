"""Tests for bundled built-in skills."""

from __future__ import annotations

from pathlib import Path

from hahobot.agent.skills import SkillsLoader


def test_builtin_skills_include_localized_companion_skills(tmp_path: Path) -> None:
    loader = SkillsLoader(tmp_path)

    names = {skill["name"] for skill in loader.list_skills(filter_unavailable=False)}

    assert {
        "translate",
        "living-together",
        "emotional-companion",
        "llm-wiki",
        "workflow-core",
        "plan",
        "verify",
        "skill-derive",
    }.issubset(names)


def test_llm_wiki_builtin_skill_loads(tmp_path: Path) -> None:
    loader = SkillsLoader(tmp_path)

    content = loader.load_skill("llm-wiki")

    assert content is not None
    assert "Treat the current workspace as a local wiki." in content


def test_living_together_is_loaded_as_always_on_skill(tmp_path: Path) -> None:
    loader = SkillsLoader(tmp_path)

    assert "living-together" in loader.get_always_skills()


def test_workflow_core_is_loaded_as_always_on_skill(tmp_path: Path) -> None:
    loader = SkillsLoader(tmp_path)

    assert "workflow-core" in loader.get_always_skills()


def test_workflow_skills_require_independent_review_for_future_task_plans(
    tmp_path: Path,
) -> None:
    loader = SkillsLoader(tmp_path)

    workflow = loader.load_skill("workflow-core") or ""
    plan = loader.load_skill("plan") or ""
    verify = loader.load_skill("verify") or ""

    assert "Do not substitute self-review for independent review" in workflow
    assert "future tasks/TODOs" in plan
    assert "item-by-item scores" in verify
