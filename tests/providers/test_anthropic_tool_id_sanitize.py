"""Tests for Anthropic tool_use/tool_result ID sanitization.

The Anthropic Messages API rejects tool IDs that do not match ``^[a-zA-Z0-9_-]+$``
with a 400. IDs from other providers or restored sessions can carry pipes/dots, so
both the tool_use id and the matching tool_result tool_use_id must be coerced to the
allowed charset — consistently, so the pair stays linked. Ported from nanobot
4d7c2074 / bdf21c93.
"""

from hahobot.providers.anthropic_provider import (
    _VALID_TOOL_ID,
    AnthropicProvider,
    _sanitize_tool_id,
)


def test_valid_id_passes_through_unchanged() -> None:
    assert _sanitize_tool_id("toolu_abc-123") == "toolu_abc-123"


def test_empty_id_passes_through() -> None:
    assert _sanitize_tool_id("") == ""


def test_invalid_chars_are_coerced_and_match_pattern() -> None:
    out = _sanitize_tool_id("call|with.dots:and|pipes")
    assert _VALID_TOOL_ID.match(out)
    assert "." not in out and "|" not in out and ":" not in out


def test_distinct_invalid_ids_do_not_collide() -> None:
    # Two ids that sanitize to the same prefix must stay distinct via the hash.
    a = _sanitize_tool_id("a.b.c")
    b = _sanitize_tool_id("a|b|c")
    assert a != b
    assert _VALID_TOOL_ID.match(a) and _VALID_TOOL_ID.match(b)


def test_sanitize_is_deterministic() -> None:
    assert _sanitize_tool_id("x.y") == _sanitize_tool_id("x.y")


def test_tool_result_block_sanitizes_tool_use_id() -> None:
    block = AnthropicProvider._tool_result_block(
        {"role": "tool", "tool_call_id": "call.with.dots", "content": "ok"}
    )
    assert _VALID_TOOL_ID.match(block["tool_use_id"])


def test_assistant_blocks_sanitize_tool_use_id_consistently() -> None:
    raw_id = "call.with.dots"
    blocks = AnthropicProvider._assistant_blocks(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": raw_id, "function": {"name": "search", "arguments": "{}"}}],
        }
    )
    tool_use = next(b for b in blocks if b["type"] == "tool_use")
    # The same raw id sanitizes identically on both sides, keeping the pair linked.
    assert tool_use["id"] == _sanitize_tool_id(raw_id)
    result = AnthropicProvider._tool_result_block(
        {"role": "tool", "tool_call_id": raw_id, "content": "done"}
    )
    assert result["tool_use_id"] == tool_use["id"]
