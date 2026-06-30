"""Regression tests for collision-resistant session storage keys.

Ported from nanobot 463f5367 / cf2f5896 / 00a907c4 / 3ce77633 / c90e4330 /
89dc34df: distinct session keys must never share a file on disk, and older
lossy-scheme files must still load (migrating only for their rightful owner).
"""

from __future__ import annotations

from hahobot.session.manager import SessionManager
from hahobot.utils.helpers import safe_filename


def test_colliding_keys_get_distinct_files(tmp_path):
    mgr = SessionManager(tmp_path)
    # Under the old replace(":", "_") scheme both keys mapped to "tg_a_b".
    k1, k2 = "tg:a_b", "tg:a:b"
    assert safe_filename(k1.replace(":", "_")) == safe_filename(k2.replace(":", "_"))

    s1 = mgr.get_or_create(k1)
    s1.add_message("user", "hello from k1")
    mgr.save(s1)

    s2 = mgr.get_or_create(k2)
    s2.add_message("user", "hello from k2")
    mgr.save(s2)

    p1 = mgr._get_session_path(k1)
    p2 = mgr._get_session_path(k2)
    assert p1 != p2
    assert p1.exists() and p2.exists()


def test_storage_key_roundtrips():
    for key in ("cli:direct", "tg:a:b", "channel/name", "中文:会话", "unified:default"):
        stem = SessionManager._storage_key(key)
        assert SessionManager._decode_storage_key(stem) == key
        # base64url stems never contain the path-unsafe ':' or '/' raw separators.
        assert ":" not in stem


def test_reload_preserves_separate_histories(tmp_path):
    mgr = SessionManager(tmp_path)
    mgr.get_or_create("tg:a_b").add_message("user", "first")
    mgr.save(mgr.get_or_create("tg:a_b"))
    mgr.get_or_create("tg:a:b").add_message("user", "second")
    mgr.save(mgr.get_or_create("tg:a:b"))

    fresh = SessionManager(tmp_path)
    assert any(m["content"] == "first" for m in fresh.get_or_create("tg:a_b").messages)
    assert any(m["content"] == "second" for m in fresh.get_or_create("tg:a:b").messages)


def test_loads_legacy_lossy_file(tmp_path):
    """A session written under the old lossy scheme still loads."""
    mgr = SessionManager(tmp_path)
    key = "cli:direct"
    legacy_path = mgr._get_legacy_lossy_path(key)
    s = mgr.get_or_create(key)
    s.add_message("user", "legacy content")
    # Simulate an old on-disk file by relocating the new save to the lossy stem.
    mgr.save(s)
    new_path = mgr._get_session_path(key)
    new_path.rename(legacy_path)
    assert legacy_path != new_path

    fresh = SessionManager(tmp_path)
    loaded = fresh.get_or_create(key)
    assert any(m["content"] == "legacy content" for m in loaded.messages)
    # Migration moved it to the collision-resistant path.
    assert new_path.exists()


def test_lossy_file_not_stolen_by_colliding_key(tmp_path):
    """A lossy file is only migrated by the key recorded inside it."""
    mgr = SessionManager(tmp_path)
    owner = "tg:a_b"
    s = mgr.get_or_create(owner)
    s.add_message("user", "owner content")
    mgr.save(s)
    # Relocate to the lossy stem shared by both colliding keys.
    legacy_path = mgr._get_legacy_lossy_path(owner)
    mgr._get_session_path(owner).rename(legacy_path)

    fresh = SessionManager(tmp_path)
    # The *other* colliding key must NOT adopt owner's history.
    other = fresh.get_or_create("tg:a:b")
    assert not any(m["content"] == "owner content" for m in other.messages)
    # The legacy file is left intact for its rightful owner.
    assert legacy_path.exists()


def test_list_sessions_recovers_keys(tmp_path):
    mgr = SessionManager(tmp_path)
    for key in ("tg:a_b", "tg:a:b"):
        s = mgr.get_or_create(key)
        s.add_message("user", f"msg {key}")
        mgr.save(s)

    keys = {row["key"] for row in mgr.list_sessions()}
    assert {"tg:a_b", "tg:a:b"} <= keys
