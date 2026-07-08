"""Regression: _read_last_entry tail-read must survive a multibyte UTF-8 split.

history.jsonl is written with ensure_ascii=False, so Chinese content produces
3-byte UTF-8 sequences. _read_last_entry seeks to size-4096 and decodes the tail;
that window can start in the middle of a character. Previously the raw
``.decode("utf-8")`` raised UnicodeDecodeError (uncaught), crashing consolidation.
"""

from __future__ import annotations

import json

from hahobot.agent.memory import MemoryStore


def _write_history_split_mid_char(store: MemoryStore) -> None:
    """Write a 2-line history.jsonl whose size-4096 boundary lands on a UTF-8
    continuation byte (0x80-0xBF), guaranteeing a mid-character tail read."""
    store.history_file.parent.mkdir(parents=True, exist_ok=True)
    # Vary the timestamp length by 0/1/2 ASCII bytes to shift the byte grid until
    # the tail window starts inside a 3-byte '汉' rather than on its lead byte.
    for pad in ("", "x", "xx"):
        first = json.dumps(
            {"cursor": 1, "timestamp": "t1" + pad, "content": "汉" * 2000},
            ensure_ascii=False,
        )
        last = json.dumps(
            {"cursor": 2, "timestamp": "t2", "content": "末尾"},
            ensure_ascii=False,
        )
        raw = (first + "\n" + last + "\n").encode("utf-8")
        boundary = raw[len(raw) - 4096]
        if len(raw) > 4096 and 0x80 <= boundary <= 0xBF:
            store.history_file.write_bytes(raw)
            return
    raise AssertionError("could not construct a mid-character tail split")


def test_read_last_entry_survives_multibyte_tail_split(tmp_path):
    store = MemoryStore(tmp_path)
    _write_history_split_mid_char(store)

    entry = store._read_last_entry()
    assert entry is not None
    assert entry["cursor"] == 2
    assert entry["content"] == "末尾"


def test_next_cursor_survives_multibyte_tail_split(tmp_path):
    store = MemoryStore(tmp_path)
    _write_history_split_mid_char(store)

    # Must not raise UnicodeDecodeError and must stay strictly monotonic.
    assert store._next_cursor() == 3
