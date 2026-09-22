from hahobot.utils.helpers import split_message


def test_split_message_nonpositive_maxlen_returns_unsplit() -> None:
    content = "alpha beta gamma delta"

    assert split_message(content, max_len=0) == [content]
    assert split_message(content, max_len=-1) == [content]


def test_split_message_preserves_indentation_and_crlf_boundaries() -> None:
    assert split_message("header\n    indented code", max_len=18) == [
        "header",
        "    indented code",
    ]
    assert split_message("abcdefg\r\n    code", max_len=8) == ["abcdefg", "    code"]
    assert split_message("abcdefgh\n    code", max_len=8) == ["abcdefgh", "    code"]


def test_split_message_drops_blank_chunks_without_losing_later_indent() -> None:
    chunks = split_message("head\n" + " " * 20 + "x", max_len=8)

    assert chunks == ["head", "    x"]
    assert all(chunk.strip() for chunk in chunks)
    assert split_message("    \nhello world", max_len=8) == ["hello", "world"]


def test_split_message_keeps_one_chunk_for_all_whitespace_input() -> None:
    assert split_message(" " * 10, max_len=4) == [" " * 4]
