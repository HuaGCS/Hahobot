"""Guard tests for the contiguous-retention invariant of retain_recent_legal_suffix.

nanobot hit a cluster of session-archive bugs (72fb642e duplicate-archive/message-loss,
baffd6ef last_consolidated miscount, 0e370241 wrong archive set) because its
``retain_recent_legal_suffix`` had a *non-contiguous* retention branch: the dropped set
could no longer be computed as a simple prefix, so callers archived the wrong messages
and miscounted ``last_consolidated``.

hahobot's implementation instead always keeps a *contiguous suffix* (and therefore drops a
contiguous prefix), which makes the upstream bugs structurally impossible: ``tail[:cut]`` in
``_split_unconsolidated`` is exactly the dropped set, and ``max(0, lc - dropped)`` is the
correct new offset. These tests lock that invariant in so a future refactor toward
non-contiguous retention would be caught here instead of silently losing messages.
"""

from hahobot.session.manager import Session


def _user(i: int) -> dict:
    return {"role": "user", "content": f"u{i}"}


def test_dropped_and_kept_exactly_partition_original_by_identity() -> None:
    msgs = [_user(i) for i in range(10)]
    session = Session(key="cli:test", messages=msgs)
    original_ids = [id(m) for m in session.messages]

    session.retain_recent_legal_suffix(max_messages=4)

    kept_ids = [id(m) for m in session.messages]
    # Kept is a contiguous suffix of the original (preserves order, no gaps).
    assert kept_ids == original_ids[len(original_ids) - len(kept_ids) :]
    # No duplicates and no silent loss: dropped + kept partition the original exactly.
    dropped_ids = [i for i in original_ids if i not in set(kept_ids)]
    assert len(dropped_ids) + len(kept_ids) == len(original_ids)
    assert set(dropped_ids).isdisjoint(kept_ids)


def test_last_consolidated_recompute_matches_identity_formula() -> None:
    # The nanobot-correct offset is "count of kept messages that were originally
    # consolidated". For a contiguous prefix drop this equals max(0, lc - dropped);
    # assert both so a non-contiguous regression (where they diverge) is caught.
    for old_lc in (0, 3, 6, 8, 10):
        msgs = [_user(i) for i in range(10)]
        session = Session(key="cli:test", messages=msgs, last_consolidated=old_lc)
        original = list(session.messages)
        before_lc = session.last_consolidated  # clamped value actually used

        session.retain_recent_legal_suffix(max_messages=4)

        kept = session.messages
        dropped_count = len(original) - len(kept)
        kept_ids = {id(m) for m in kept}
        identity_lc = sum(1 for i, m in enumerate(original) if i < before_lc and id(m) in kept_ids)
        assert session.last_consolidated == identity_lc
        assert session.last_consolidated == max(0, before_lc - dropped_count)


def test_mid_turn_cutoff_extends_back_to_user_without_loss() -> None:
    # Tail ending in assistant-only turns is exactly the shape that broke nanobot's
    # non-contiguous branch. Build user/assistant turns and force a mid-turn cutoff.
    msgs: list[dict] = []
    for i in range(6):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    session = Session(key="cli:test", messages=msgs)
    original_ids = [id(m) for m in session.messages]

    session.retain_recent_legal_suffix(max_messages=3)

    kept = session.messages
    # Cutoff must not start mid-turn: first kept message is a user turn.
    assert kept[0]["role"] == "user"
    kept_ids = [id(m) for m in kept]
    # Still a clean contiguous suffix partition (no dup, no loss).
    assert kept_ids == original_ids[len(original_ids) - len(kept_ids) :]


def test_orphan_tool_results_trimmed_from_front_are_dropped_not_duplicated() -> None:
    # Retained window starting with an orphan tool result must trim it from the front;
    # the trimmed messages are dropped (archived), never kept twice.
    msgs = [
        {"role": "user", "content": "u0"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "tool_call_id": "orphan", "content": "orphan-result"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    session = Session(key="cli:test", messages=msgs)
    original_ids = [id(m) for m in session.messages]

    # max=3 lands the window on the orphan tool result; it must be trimmed off the front.
    session.retain_recent_legal_suffix(max_messages=3)

    kept = session.messages
    assert all(not (m.get("role") == "tool" and m.get("tool_call_id") == "orphan") for m in kept)
    kept_ids = [id(m) for m in kept]
    assert kept_ids == original_ids[len(original_ids) - len(kept_ids) :]
    assert len(set(kept_ids)) == len(kept_ids)  # no duplicates
