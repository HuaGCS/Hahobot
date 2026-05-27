"""Tests for the server-side new_facts → structured fragment wrapper."""

from __future__ import annotations

from hahobot.agent.memory import _format_new_facts_as_fragments
from hahobot.agent.memory_facts_sqlite import parse_memory_fragments

NOW = "2026-05-26T17:30"


def test_wraps_plain_bullets_with_default_tag() -> None:
    out = _format_new_facts_as_fragments(
        "User prefers concise replies.",
        src="turn",
        now_iso=NOW,
    )
    assert out.startswith(f"<!-- ts:{NOW} tag:preference src:turn -->")
    assert "User prefers concise replies." in out
    fragments = parse_memory_fragments(out, default_ts="ignored")
    assert len(fragments) == 1
    assert fragments[0]["tag"] == "preference"
    assert fragments[0]["src"] == "turn"


def test_honors_llm_emitted_tag_header() -> None:
    text = (
        "<!-- tag:project -->\n"
        "Replace Mem0 with SQLite-FTS.\n"
        "\n"
        "<!-- tag:reference -->\n"
        "Admin entry is in field_specs.py.\n"
    )
    out = _format_new_facts_as_fragments(text, src="turn", now_iso=NOW)
    fragments = parse_memory_fragments(out, default_ts="ignored")
    assert [f["tag"] for f in fragments] == ["project", "reference"]
    assert all(f["src"] == "turn" for f in fragments)
    assert all(f["ts"] == NOW for f in fragments)


def test_overrides_llm_emitted_ts_and_src_tokens() -> None:
    text = (
        "<!-- ts:1999-01-01T00:00 tag:project src:malicious -->\n"
        "body content\n"
    )
    out = _format_new_facts_as_fragments(text, src="dream", now_iso=NOW)
    fragments = parse_memory_fragments(out, default_ts="ignored")
    assert fragments[0]["ts"] == NOW
    assert fragments[0]["src"] == "dream"
    assert fragments[0]["tag"] == "project"


def test_unknown_tag_falls_back_to_default() -> None:
    text = "<!-- tag:weird_unsanctioned_value -->\nbody"
    out = _format_new_facts_as_fragments(text, src="turn", now_iso=NOW)
    fragments = parse_memory_fragments(out, default_ts="ignored")
    assert fragments[0]["tag"] == "preference"


def test_multiple_blank_separated_blocks_become_multiple_fragments() -> None:
    text = "first fact\n\nsecond fact\n\n\nthird fact"
    out = _format_new_facts_as_fragments(text, src="turn", now_iso=NOW)
    fragments = parse_memory_fragments(out, default_ts="ignored")
    assert len(fragments) == 3
    assert all(f["src"] == "turn" for f in fragments)


def test_empty_input_returns_empty_string() -> None:
    assert _format_new_facts_as_fragments("", src="turn", now_iso=NOW) == ""
    assert _format_new_facts_as_fragments("   \n\n   ", src="turn", now_iso=NOW) == ""


def test_header_only_block_is_dropped() -> None:
    text = "<!-- tag:project -->\n"
    out = _format_new_facts_as_fragments(text, src="turn", now_iso=NOW)
    assert out == ""
