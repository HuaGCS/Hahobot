"""Tests for AnthropicProvider._merge_consecutive."""

from hahobot.providers.anthropic_provider import AnthropicProvider


def test_merge_consecutive_preserves_basic_alternation() -> None:
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "bye"},
    ]
    result = AnthropicProvider._merge_consecutive(msgs)
    assert [msg["role"] for msg in result] == ["user", "assistant", "user"]


def test_merge_consecutive_strips_trailing_assistant_prefill() -> None:
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    result = AnthropicProvider._merge_consecutive(msgs)
    assert result == [{"role": "user", "content": "hello"}]


def test_merge_consecutive_reroutes_single_assistant_to_user() -> None:
    msgs = [{"role": "assistant", "content": "hi"}]
    result = AnthropicProvider._merge_consecutive(msgs)
    assert result == [{"role": "user", "content": "hi"}]


def test_merge_consecutive_preserves_empty_result_for_tool_use_only_assistant() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "let me search"},
                {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
            ],
        }
    ]
    result = AnthropicProvider._merge_consecutive(msgs)
    assert result == []


def test_merge_consecutive_prepends_synthetic_user_for_leading_assistant() -> None:
    msgs = [
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "ok"},
        {"role": "assistant", "content": "reply"},
    ]
    result = AnthropicProvider._merge_consecutive(msgs)
    assert [msg["role"] for msg in result] == ["user", "assistant", "user"]
    assert result[0]["content"] == "(conversation continued)"
    assert result[1]["content"] == "hi"
    assert result[2]["content"] == "ok"


def test_merge_consecutive_leaves_tool_use_leading_assistant_unchanged() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            ],
        },
    ]
    result = AnthropicProvider._merge_consecutive(msgs)
    assert [msg["role"] for msg in result] == ["assistant", "user"]
