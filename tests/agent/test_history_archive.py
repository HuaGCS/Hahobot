from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hahobot.agent.history_archive import HistoryArchiveStore
from hahobot.agent.memory import MemoryStore
from hahobot.agent.tools.history import HistoryExpandTool, HistorySearchTool, HistoryTimelineTool
from hahobot.bus.events import InboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.providers.base import GenerationSettings, LLMResponse, ToolCallRequest


def _messages() -> list[dict]:
    return [
        {
            "role": "user",
            "content": "Please review `hahobot/agent/loop.py` and providerPool behavior.",
            "timestamp": "2026-03-30T12:00:01",
        },
        {
            "role": "assistant",
            "content": "I will inspect /status and providerPool handling first.",
            "timestamp": "2026-03-30T12:00:05",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "read_file"}}
            ],
        },
        {
            "role": "tool",
            "name": "read_file",
            "content": "contents of hahobot/agent/loop.py",
            "timestamp": "2026-03-30T12:00:10",
        },
        {
            "role": "assistant",
            "content": "The admin page and providerPool flow both need updates.",
            "timestamp": "2026-03-30T12:00:20",
        },
    ]


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _make_memory_tool_response(summary: str) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(
                id="call_1",
                name="save_memory",
                arguments={
                    "history_entry": summary,
                    "memory_update": "# Memory\n- providerPool takes precedence when configured.",
                },
            )
        ],
    )


@pytest.mark.asyncio
async def test_memory_store_consolidate_writes_archive_sidecar(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    provider = AsyncMock()
    provider.chat_with_retry = AsyncMock(
        return_value=_make_memory_tool_response(
            "[2026-03-30 12:00] Reviewed providerPool handling in hahobot/agent/loop.py and "
            "admin flows."
        )
    )
    archive = HistoryArchiveStore(tmp_path)
    messages = _messages()

    result = await store.consolidate(
        messages,
        provider,
        "test-model",
        on_archive=lambda payload: archive.write_archive(
            session_key="cli:direct",
            messages=messages,
            history_entry=payload["history_entry"],
            source="token_consolidation",
            raw_archive=bool(payload.get("raw_archive")),
        ),
    )

    assert result is True
    index_path = tmp_path / "memory" / "archive" / "index.jsonl"
    assert index_path.exists()
    entries = _read_jsonl(index_path)
    assert len(entries) == 1
    assert entries[0]["sessionKey"] == "cli:direct"
    assert entries[0]["source"] == "token_consolidation"
    assert "providerPool" in entries[0]["summary"]

    chunk_path = tmp_path / "memory" / "archive" / entries[0]["chunkPath"]
    chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
    assert len(chunk["messages"]) == 4
    assert chunk["messages"][2]["name"] == "read_file"


@pytest.mark.asyncio
async def test_archive_sidecar_strips_private_blocks_and_adds_observation(tmp_path: Path) -> None:
    store = HistoryArchiveStore(tmp_path)
    archive_id = store.write_archive(
        session_key="cli:direct",
        messages=[
            {
                "role": "user",
                "content": "Fix `hahobot/agent/loop.py`. <private>token=secret</private>",
                "timestamp": "2026-03-30T12:00:01",
            },
            {
                "role": "assistant",
                "content": "Implemented the bug fix in hahobot/agent/loop.py.",
                "timestamp": "2026-03-30T12:00:20",
            },
        ],
        history_entry=(
            "[2026-03-30 12:00] Fixed bug in hahobot/agent/loop.py. "
            "<private>secret summary</private>"
        ),
        source="token_consolidation",
    )

    entries = _read_jsonl(tmp_path / "memory" / "archive" / "index.jsonl")
    entry = entries[0]
    assert entry["observationType"] == "bugfix"
    assert "hahobot/agent/loop.py" in entry["files"]
    assert entry["facts"]
    assert "secret" not in json.dumps(entry, ensure_ascii=False)

    chunk = json.loads(
        (tmp_path / "memory" / "archive" / entry["chunkPath"]).read_text(encoding="utf-8")
    )
    assert chunk["observation"]["type"] == "bugfix"
    assert chunk["observation"]["files"] == entry["files"]
    assert archive_id is not None
    assert "secret" not in json.dumps(chunk, ensure_ascii=False)


@pytest.mark.asyncio
async def test_history_search_prioritizes_current_session(tmp_path: Path) -> None:
    store = HistoryArchiveStore(tmp_path)
    store.write_archive(
        session_key="cli:other",
        messages=_messages(),
        history_entry="[2026-03-30 12:00] providerPool discussion from another session.",
        source="token_consolidation",
    )
    current_id = store.write_archive(
        session_key="cli:direct",
        messages=_messages(),
        history_entry="[2026-03-30 12:01] providerPool discussion in the active session.",
        source="token_consolidation",
    )

    tool = HistorySearchTool(tmp_path)
    tool.set_context("cli", "direct", "default")
    output = await tool.execute("providerPool", limit=2)

    assert output.startswith('Archived history matches for "providerPool":')
    first_id = re.search(r"1\. ID: ([^\n]+)", output)
    assert first_id is not None
    assert first_id.group(1).strip() == current_id
    assert "Type:" in output
    assert "Next: call history_timeline" in output


@pytest.mark.asyncio
async def test_history_search_filters_by_file_and_timeline(tmp_path: Path) -> None:
    store = HistoryArchiveStore(tmp_path)
    loop_id = store.write_archive(
        session_key="cli:direct",
        messages=_messages(),
        history_entry="[2026-03-30 12:00] Fixed providerPool behavior in hahobot/agent/loop.py.",
        source="token_consolidation",
    )
    store.write_archive(
        session_key="cli:direct",
        messages=[{"role": "user", "content": "Discuss README.md", "timestamp": "2026-03-30T13:00:00"}],
        history_entry="[2026-03-30 13:00] Updated README.md docs.",
        source="token_consolidation",
    )

    search = HistorySearchTool(tmp_path)
    search.set_context("cli", "direct", "default")
    output = await search.execute("providerPool", file="hahobot/agent/loop.py", limit=5)

    assert loop_id in output
    assert "README.md" not in output

    timeline = HistoryTimelineTool(tmp_path)
    timeline.set_context("cli", "direct", "default")
    timeline_output = await timeline.execute(file="hahobot/agent/loop.py", limit=5)

    assert "Archived history timeline" in timeline_output
    assert f"id={loop_id}" in timeline_output
    assert "files: hahobot/agent/loop.py" in timeline_output


@pytest.mark.asyncio
async def test_history_expand_survives_new_session_clear(tmp_path: Path) -> None:
    from hahobot.agent.loop import AgentLoop

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings(max_tokens=256)
    provider.chat_with_retry = AsyncMock(
        return_value=_make_memory_tool_response(
            "[2026-03-30 12:00] Reviewed providerPool handling in hahobot/agent/loop.py."
        )
    )
    provider.chat_stream_with_retry = AsyncMock(
        return_value=SimpleNamespace(
            has_tool_calls=False,
            content="ok",
            finish_reason="stop",
            reasoning_content=None,
            thinking_blocks=None,
            tool_calls=[],
            usage=None,
        )
    )

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=1024,
    )

    session = loop.sessions.get_or_create("cli:test")
    session.messages = _messages()
    loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="test", content="/new")
    )
    assert response is not None
    assert "new session started" in response.content.lower()

    session_after = loop.sessions.get_or_create("cli:test")
    assert session_after.messages == []

    await loop.close_mcp()

    search_tool = HistorySearchTool(tmp_path)
    search_tool.set_context("cli", "test", "default")
    search_output = await search_tool.execute("providerPool", limit=1)
    match = re.search(r"1\. ID: ([^\n]+)", search_output)
    assert match is not None

    expand_tool = HistoryExpandTool(tmp_path)
    expand_tool.set_context("cli", "test", "default")
    expand_output = await expand_tool.execute(match.group(1).strip(), maxMessages=10)

    assert "Archived transcript" in expand_output
    assert "providerPool behavior" in expand_output
    assert "TOOL(read_file)" in expand_output


@pytest.mark.asyncio
async def test_history_expand_reports_missing_chunk(tmp_path: Path) -> None:
    store = HistoryArchiveStore(tmp_path)
    archive_id = store.write_archive(
        session_key="cli:direct",
        messages=_messages(),
        history_entry="[2026-03-30 12:00] providerPool discussion.",
        source="token_consolidation",
    )
    entries = _read_jsonl(tmp_path / "memory" / "archive" / "index.jsonl")
    chunk_path = tmp_path / "memory" / "archive" / entries[0]["chunkPath"]
    chunk_path.unlink()

    tool = HistoryExpandTool(tmp_path)
    tool.set_context("cli", "direct", "default")
    output = await tool.execute(archive_id or "")

    assert output.startswith("Error:")
    assert "missing" in output.lower()


def test_history_archive_sqlite_index_search_and_rebuild(tmp_path: Path) -> None:
    store = HistoryArchiveStore(tmp_path)
    loop_id = store.write_archive(
        session_key="cli:direct",
        messages=_messages(),
        history_entry="[2026-03-30 12:00] Fixed providerPool behavior in hahobot/agent/loop.py.",
        source="token_consolidation",
    )
    store.write_archive(
        session_key="cli:direct",
        messages=[
            {
                "role": "user",
                "content": "Discuss README.md",
                "timestamp": "2026-03-30T13:00:00",
            }
        ],
        history_entry="[2026-03-30 13:00] Updated README.md docs.",
        source="token_consolidation",
    )

    sqlite_store = HistoryArchiveStore(tmp_path, index_backend="sqlite")
    assert sqlite_store.rebuild_sqlite_index() == 2

    index_path = tmp_path / "memory" / "archive" / "index.sqlite"
    assert index_path.exists()

    matches = sqlite_store.search(query="providerPool", file="hahobot/agent/loop.py", limit=5)
    assert [match["id"] for match in matches] == [loop_id]

    timeline_matches = sqlite_store.search(query="", file="hahobot/agent/loop.py", limit=5)
    assert [match["id"] for match in timeline_matches] == [loop_id]


def test_history_archive_sqlite_falls_back_to_jsonl_on_corrupt_index(tmp_path: Path) -> None:
    store = HistoryArchiveStore(tmp_path)
    archive_id = store.write_archive(
        session_key="cli:direct",
        messages=_messages(),
        history_entry="[2026-03-30 12:00] providerPool discussion in hahobot/agent/loop.py.",
        source="token_consolidation",
    )

    sqlite_store = HistoryArchiveStore(tmp_path, index_backend="sqlite")
    sqlite_store.rebuild_sqlite_index()
    (tmp_path / "memory" / "archive" / "index.sqlite").write_text("not sqlite", encoding="utf-8")

    matches = sqlite_store.search(query="providerPool", file="hahobot/agent/loop.py", limit=5)

    assert [match["id"] for match in matches] == [archive_id]
