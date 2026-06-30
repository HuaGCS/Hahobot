"""Typeless content blocks are coerced to text for the Anthropic Messages API.

The API rejects any content block without a "type" field
("content.0.type: Field required"). A tool that returns a bare dict (no "type")
would otherwise be forwarded verbatim and 400 the request. Both the assistant
and user conversion paths now stringify such blocks as JSON text.
Ported from nanobot efb792ff / 00a7de01.
"""

from __future__ import annotations

import json

from hahobot.providers.anthropic_provider import AnthropicProvider


def test_assistant_blocks_coerces_typeless_dict() -> None:
    msg = {"role": "assistant", "content": [{"result": "ok", "n": 1}]}
    blocks = AnthropicProvider._assistant_blocks(msg)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert json.loads(blocks[0]["text"]) == {"result": "ok", "n": 1}


def test_assistant_blocks_keeps_typed_dict() -> None:
    msg = {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}
    blocks = AnthropicProvider._assistant_blocks(msg)
    assert blocks == [{"type": "text", "text": "hi"}]


def test_convert_user_content_coerces_typeless_dict() -> None:
    result = AnthropicProvider._convert_user_content([{"foo": "bar"}])
    assert isinstance(result, list)
    assert result[0]["type"] == "text"
    assert json.loads(result[0]["text"]) == {"foo": "bar"}


def test_convert_user_content_keeps_typed_dict() -> None:
    result = AnthropicProvider._convert_user_content([{"type": "text", "text": "hi"}])
    assert result == [{"type": "text", "text": "hi"}]


def test_stringify_handles_non_serializable() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird-obj"

    text = AnthropicProvider._stringify_typeless_block({"v": Weird()})
    assert "weird-obj" in text
