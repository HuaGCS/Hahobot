from hahobot.utils.helpers import split_message


def test_split_message_nonpositive_maxlen_returns_unsplit() -> None:
    content = "alpha beta gamma delta"

    assert split_message(content, max_len=0) == [content]
    assert split_message(content, max_len=-1) == [content]
