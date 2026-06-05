"""Regression tests for last_consolidated offset hardening.

Ported from nanobot 0307ee6 / 13178f3: a corrupt or out-of-range
``last_consolidated`` offset must not hide history or crash slicing.
"""

from hahobot.session.manager import Session


def _msgs(n: int) -> list[dict]:
    return [{"role": "user", "content": f"m{i}"} for i in range(n)]


def test_out_of_range_offset_is_reset() -> None:
    session = Session(key="cli:test", messages=_msgs(1), last_consolidated=5)
    # Offset 5 exceeds the single loaded message; reset to avoid hiding history.
    assert session.last_consolidated == 0
    assert len(session.get_history()) == 1


def test_negative_offset_is_reset() -> None:
    session = Session(key="cli:test", messages=_msgs(3), last_consolidated=-5)
    assert session.last_consolidated == 0
    assert len(session.get_history()) == 3


def test_non_integer_offset_is_reset() -> None:
    session = Session(key="cli:test", messages=_msgs(3), last_consolidated="2")  # type: ignore[arg-type]
    assert session.last_consolidated == 0
    assert len(session.get_history()) == 3


def test_bool_offset_is_reset() -> None:
    # bool is an int subclass; True would slice as offset 1 and hide a message.
    session = Session(key="cli:test", messages=_msgs(2), last_consolidated=True)  # type: ignore[arg-type]
    assert session.last_consolidated == 0


def test_valid_offset_is_preserved() -> None:
    session = Session(key="cli:test", messages=_msgs(5), last_consolidated=2)
    assert session.last_consolidated == 2
    assert len(session.get_history()) == 3


def test_offset_equal_to_length_is_preserved() -> None:
    # Fully consolidated session: offset == len(messages) is a legal boundary.
    session = Session(key="cli:test", messages=_msgs(4), last_consolidated=4)
    assert session.last_consolidated == 4
    assert session.get_history() == []
