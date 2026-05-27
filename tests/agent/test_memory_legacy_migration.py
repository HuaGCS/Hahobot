"""Tests for the legacy MEMORY.md migration helpers."""

from __future__ import annotations

from pathlib import Path

from hahobot.agent.memory import (
    migrate_legacy_memory_file,
    migrate_legacy_memory_workspace,
)
from hahobot.agent.memory_facts_sqlite import parse_memory_fragments


def test_migrate_legacy_only_file_wraps_every_fragment(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    path.write_text("first plain fact.\n\nsecond plain fact.\n", encoding="utf-8")
    summary = migrate_legacy_memory_file(path)
    assert summary["changed"] is True
    assert summary["migrated"] == 2
    assert summary["preserved"] == 0
    text = path.read_text(encoding="utf-8")
    fragments = parse_memory_fragments(text, default_ts="ignored")
    assert {f["tag"] for f in fragments} == {"legacy"}
    assert {f["src"] for f in fragments} == {"migration"}
    assert summary["backup"] is not None
    assert Path(summary["backup"]).exists()


def test_migrate_skips_already_structured_files(tmp_path: Path) -> None:
    body = (
        "<!-- ts:2026-05-26T17:30 tag:preference src:turn -->\n"
        "structured fact one\n"
        "\n"
        "<!-- ts:2026-05-26T18:00 tag:project src:dream -->\n"
        "structured fact two\n"
    )
    path = tmp_path / "MEMORY.md"
    path.write_text(body, encoding="utf-8")
    summary = migrate_legacy_memory_file(path)
    assert summary["changed"] is False
    assert summary["migrated"] == 0
    assert summary["preserved"] == 2
    assert summary["backup"] is None
    assert path.read_text(encoding="utf-8") == body


def test_migrate_mixed_file_wraps_legacy_and_preserves_structured(tmp_path: Path) -> None:
    body = (
        "<!-- ts:2026-05-26T17:30 tag:preference src:turn -->\n"
        "kept structured fragment\n"
        "\n"
        "plain legacy fragment\n"
    )
    path = tmp_path / "MEMORY.md"
    path.write_text(body, encoding="utf-8")
    summary = migrate_legacy_memory_file(path)
    assert summary["changed"] is True
    assert summary["migrated"] == 1
    assert summary["preserved"] == 1
    fragments = parse_memory_fragments(path.read_text(encoding="utf-8"), default_ts="ignored")
    assert fragments[0]["tag"] == "preference"
    assert fragments[0]["src"] == "turn"
    assert fragments[1]["tag"] == "legacy"
    assert fragments[1]["src"] == "migration"


def test_migrate_missing_file_is_noop(tmp_path: Path) -> None:
    summary = migrate_legacy_memory_file(tmp_path / "no-such-MEMORY.md")
    assert summary["exists"] is False
    assert summary["changed"] is False
    assert summary["migrated"] == 0


def test_migrate_workspace_iterates_personas(tmp_path: Path) -> None:
    workspace = tmp_path
    (workspace / "memory").mkdir()
    (workspace / "memory" / "MEMORY.md").write_text(
        "default persona plain fact.\n", encoding="utf-8"
    )
    persona_dir = workspace / "personas" / "alice" / "memory"
    persona_dir.mkdir(parents=True)
    (persona_dir / "MEMORY.md").write_text(
        "alice plain fact.\n\n<!-- ts:2026-05-26T17:30 tag:preference src:turn -->\n"
        "alice structured fact\n",
        encoding="utf-8",
    )

    summary = migrate_legacy_memory_workspace(workspace)
    assert summary["files_changed"] == 2
    assert summary["total_migrated"] == 2
    assert summary["total_preserved"] == 1
    personas_seen = {r["persona"] for r in summary["results"]}
    assert "default" in personas_seen
    assert "alice" in personas_seen


def test_migrate_idempotent_when_rerun(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    path.write_text("first\n\nsecond\n", encoding="utf-8")
    first = migrate_legacy_memory_file(path)
    assert first["changed"] is True

    second = migrate_legacy_memory_file(path)
    assert second["changed"] is False
    assert second["migrated"] == 0
    assert second["preserved"] == 2
