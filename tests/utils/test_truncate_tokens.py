"""Tests for truncate_text_to_tokens (token-budget history cap).

A character cap silently lets a section blow past its intended size on CJK /
code-heavy content; a token cap holds regardless of content. Ported from
nanobot 973a5ee5.
"""

from hahobot.utils.helpers import _TIKTOKEN_ENC, truncate_text_to_tokens


def test_short_text_unchanged() -> None:
    assert truncate_text_to_tokens("hello world", 1000) == "hello world"


def test_empty_text_unchanged() -> None:
    assert truncate_text_to_tokens("", 1000) == ""


def test_zero_budget_returns_input() -> None:
    assert truncate_text_to_tokens("hello", 0) == "hello"


def test_truncates_long_text_to_token_budget() -> None:
    text = "word " * 5000
    out = truncate_text_to_tokens(text, 100)
    assert out != text
    assert out.endswith("... (truncated)")
    # The decoded prefix must not exceed the budget.
    body = out[: -len("\n... (truncated)")]
    assert len(_TIKTOKEN_ENC.encode(body)) <= 100


def test_cjk_text_capped_by_tokens_not_chars() -> None:
    # CJK content: a char cap would allow far more tokens than intended.
    text = "你好世界" * 4000
    out = truncate_text_to_tokens(text, 200)
    assert out.endswith("... (truncated)")
    body = out[: -len("\n... (truncated)")]
    assert len(_TIKTOKEN_ENC.encode(body)) <= 200
