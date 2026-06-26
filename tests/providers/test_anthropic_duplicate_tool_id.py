"""Tests for Anthropic duplicate tool_use id dropping in ``_parse_response``.

The Anthropic Messages API 400s ("tool_use ids must be unique") on any request whose
assistant turn carries two tool_use blocks sharing an id. A mis-assembled stream can
surface the same block twice; persisting it verbatim re-sends the malformed turn on every
subsequent request and permanently bricks the session. ``_parse_response`` drops the
duplicate (keeps the first) as the response enters hahobot. Both chat() and chat_stream()
funnel through ``_parse_response``. Ported from nanobot 6689e2d3.
"""

from __future__ import annotations

from types import SimpleNamespace

from hahobot.providers.anthropic_provider import AnthropicProvider


def _block(**kwargs):
    return SimpleNamespace(**kwargs)


def _response(content, stop_reason="tool_use"):
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=None)


def test_duplicate_tool_use_id_is_dropped() -> None:
    resp = _response(
        [
            _block(type="tool_use", id="toolu_1", name="search", input={"q": "a"}),
            _block(type="tool_use", id="toolu_1", name="search", input={"q": "b"}),
        ]
    )
    parsed = AnthropicProvider._parse_response(resp)
    assert [tc.id for tc in parsed.tool_calls] == ["toolu_1"]
    # The first block wins; the duplicate is discarded entirely.
    assert parsed.tool_calls[0].arguments == {"q": "a"}


def test_distinct_tool_use_ids_are_all_kept() -> None:
    resp = _response(
        [
            _block(type="tool_use", id="toolu_1", name="search", input={"q": "a"}),
            _block(type="tool_use", id="toolu_2", name="search", input={"q": "b"}),
        ]
    )
    parsed = AnthropicProvider._parse_response(resp)
    assert [tc.id for tc in parsed.tool_calls] == ["toolu_1", "toolu_2"]


def test_text_and_tool_blocks_coexist_with_dedupe() -> None:
    resp = _response(
        [
            _block(type="text", text="working on it"),
            _block(type="tool_use", id="toolu_x", name="run", input={}),
            _block(type="tool_use", id="toolu_x", name="run", input={}),
        ]
    )
    parsed = AnthropicProvider._parse_response(resp)
    assert parsed.content == "working on it"
    assert [tc.id for tc in parsed.tool_calls] == ["toolu_x"]
