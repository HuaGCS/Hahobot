"""Tests for Telegram Markdown/HTML-aware splitting."""

from hahobot.channels.telegram import (
    TELEGRAM_HTML_MAX_LEN,
    _markdown_to_telegram_html,
    _split_telegram_markdown,
    _split_telegram_markdown_html_chunks,
)


def test_short_content_single_chunk():
    """Short content returns a single chunk unchanged."""
    result = _split_telegram_markdown("Hello, world!", 4000)
    assert result == ["Hello, world!"]


def test_short_content_at_limit():
    """Content exactly at max_len returns a single chunk."""
    text = "a" * 100
    result = _split_telegram_markdown(text, 100)
    assert result == [text]


def test_short_content_empty():
    """Empty content returns empty list."""
    assert _split_telegram_markdown("", 100) == []
    assert _split_telegram_markdown("   ", 100) == []


def test_plain_text_splits_at_newline():
    """Plain text splits at newline boundaries without exceeding max_len."""
    text = "abc\ndef\nghi\njkl\nmno\npqr\nstu\nvwx\nyz\n"
    max_len = 30
    result = _split_telegram_markdown(text, max_len)
    assert len(result) >= 2
    for chunk in result:
        assert len(chunk) <= max_len


def test_plain_text_splits_at_space_when_no_newline():
    """Plain text splits at space when no newline is near the boundary."""
    words = ["word"] * 20
    text = " ".join(words)
    max_len = 50
    result = _split_telegram_markdown(text, max_len)
    for chunk in result:
        assert len(chunk) <= max_len


def test_code_block_balanced_across_chunks():
    """When a long fenced code block is split, every chunk has balanced fences,
    and reopened chunks start with the original fence line."""
    line = "some text before\n```python\n" + "x = 1\n" * 60 + "```\nmore text after"
    max_len = 120
    result = _split_telegram_markdown(line, max_len)
    # Every chunk must have an even number of ```
    for chunk in result:
        assert chunk.count("```") % 2 == 0, (
            f"Unbalanced fences in chunk: {chunk[:50]!r}...  count={chunk.count('```')}"
        )
    # First chunk should end with a closing ```
    # A later chunk should start with ```python (re-open)
    fence_reopen = [c for c in result if c.startswith("```python")]
    assert fence_reopen, "Expected a chunk starting with ```python to re-open the code block"
    assert len(fence_reopen) >= 1


def test_code_block_no_language():
    """Fenced code block without language specifier stays balanced across splits."""
    text = "start\n```\n" + "data\n" * 60 + "```\nend"
    max_len = 80
    result = _split_telegram_markdown(text, max_len)
    for chunk in result:
        assert chunk.count("```") % 2 == 0, (
            f"Unbalanced fences in chunk: {chunk[:50]!r}...  count={chunk.count('```')}"
        )


def test_code_block_reopened_with_correct_fence():
    """Re-opened chunk preserves the exact fence line (e.g. ```python)."""
    text = "intro\n```python\n" + "code\n" * 40 + "```\n"
    max_len = 100
    result = _split_telegram_markdown(text, max_len)
    reopened = [c for c in result if c.startswith("```python")]
    assert reopened, "No chunk re-opens with ```python"
    for r in reopened:
        assert r.startswith("```python"), f"Re-open line should be ```python, got {r[:20]!r}"


def test_code_block_closing_budget():
    """When close to max_len boundary, the close fence fits within the limit."""
    text = "```\n" + "a\n" * 50 + "```\n"
    max_len = 60
    result = _split_telegram_markdown(text, max_len)
    for chunk in result:
        assert chunk.count("```") % 2 == 0
        assert len(chunk) <= max_len


def test_rendered_html_chunks_stay_within_telegram_limit():
    text = "**bold** " * 501
    assert len(_markdown_to_telegram_html(text)) > TELEGRAM_HTML_MAX_LEN

    chunks = _split_telegram_markdown_html_chunks(text, TELEGRAM_HTML_MAX_LEN)

    assert len(chunks) > 1
    assert all(len(html) <= TELEGRAM_HTML_MAX_LEN for _, html in chunks)
    assert all("<b>" not in markdown for markdown, _ in chunks)
