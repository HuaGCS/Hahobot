from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from hahobot.session.manager import SessionManager


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_save_appends_only_new_messages(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("qq:test")
    session.add_message("user", "hello")
    session.add_message("assistant", "hi")
    manager.save(session)

    path = manager._get_session_path(session.key)
    original_text = path.read_text(encoding="utf-8")

    session.add_message("user", "next")
    manager.save(session)

    lines = _read_jsonl(path)
    assert path.read_text(encoding="utf-8").startswith(original_text)
    assert sum(1 for line in lines if line.get("_type") == "metadata") == 1
    assert [line["content"] for line in lines if line.get("role")] == ["hello", "hi", "next"]


def test_save_appends_metadata_checkpoint_without_rewriting_history(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("qq:test")
    session.add_message("user", "hello")
    session.add_message("assistant", "hi")
    manager.save(session)

    path = manager._get_session_path(session.key)
    original_text = path.read_text(encoding="utf-8")

    session.last_consolidated = 2
    manager.save(session)

    lines = _read_jsonl(path)
    assert path.read_text(encoding="utf-8").startswith(original_text)
    assert sum(1 for line in lines if line.get("_type") == "metadata") == 2
    assert lines[-1]["_type"] == "metadata"
    assert lines[-1]["last_consolidated"] == 2

    manager.invalidate(session.key)
    reloaded = manager.get_or_create("qq:test")
    assert reloaded.last_consolidated == 2
    assert [message["content"] for message in reloaded.messages] == ["hello", "hi"]


def test_clear_rewrites_session_file(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("qq:test")
    session.add_message("user", "hello")
    session.add_message("assistant", "hi")
    manager.save(session)

    path = manager._get_session_path(session.key)
    session.clear()
    manager.save(session)

    lines = _read_jsonl(path)
    assert len(lines) == 1
    assert lines[0]["_type"] == "metadata"
    assert lines[0]["last_consolidated"] == 0

    manager.invalidate(session.key)
    reloaded = manager.get_or_create("qq:test")
    assert reloaded.messages == []
    assert reloaded.last_consolidated == 0


def test_list_sessions_uses_file_mtime_for_append_only_updates(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("qq:test")
    session.add_message("user", "hello")
    manager.save(session)

    path = manager._get_session_path(session.key)
    stale_time = time.time() - 3600
    os.utime(path, (stale_time, stale_time))

    before = datetime.fromisoformat(manager.list_sessions()[0]["updated_at"])
    assert before.timestamp() < time.time() - 3000

    session.add_message("assistant", "hi")
    manager.save(session)

    after = datetime.fromisoformat(manager.list_sessions()[0]["updated_at"])
    assert after > before


def test_full_rewrite_is_atomic_when_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("qq:test")
    session.add_message("user", "hello")
    session.add_message("assistant", "hi")
    manager.save(session)

    path = manager._get_session_path(session.key)
    original_text = path.read_text(encoding="utf-8")
    session.clear()

    original_write = manager._write_jsonl_line
    seen = {"count": 0}

    def _boom(handle, payload):
        original_write(handle, payload)
        seen["count"] += 1
        if seen["count"] == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(manager, "_write_jsonl_line", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        manager.save(session)

    assert path.read_text(encoding="utf-8") == original_text
    assert list(path.parent.glob("*.tmp")) == []


def test_load_repairs_corrupt_session_and_rewrites_clean_file(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    path = manager._get_session_path("qq:test")
    path.parent.mkdir(parents=True, exist_ok=True)
    created_at = "2026-04-20T00:00:00"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "key": "qq:test",
                        "created_at": created_at,
                        "updated_at": created_at,
                        "metadata": {"persona": "Aria"},
                        "last_consolidated": 0,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "role": "user",
                        "content": "hello",
                        "timestamp": created_at,
                    },
                    ensure_ascii=False,
                ),
                '{"role":"assistant","content":"broken"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = manager.get_or_create("qq:test")

    assert [message["content"] for message in session.messages] == ["hello"]
    assert session.metadata["persona"] == "Aria"
    assert session._requires_full_save is True

    session.add_message("assistant", "fixed")
    manager.save(session)

    lines = _read_jsonl(path)
    assert sum(1 for line in lines if line.get("_type") == "metadata") == 1
    assert [line["content"] for line in lines if line.get("role")] == ["hello", "fixed"]


def test_list_sessions_recovers_corrupt_first_line(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    path = manager._get_session_path("cli:broken")
    path.parent.mkdir(parents=True, exist_ok=True)
    created_at = "2026-04-20T01:23:45"
    path.write_text(
        "\n".join(
            [
                '{"_type":"metadata"',
                json.dumps(
                    {
                        "_type": "metadata",
                        "key": "cli:broken",
                        "created_at": created_at,
                        "updated_at": created_at,
                        "metadata": {},
                        "last_consolidated": 0,
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    sessions = manager.list_sessions()

    assert sessions
    assert sessions[0]["key"] == "cli:broken"
    assert sessions[0]["created_at"] == created_at
