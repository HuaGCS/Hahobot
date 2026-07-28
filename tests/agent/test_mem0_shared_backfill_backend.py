"""Tests for durable, idempotent Mem0 memory backfill delivery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hahobot.agent.memory_backends.mem0_backend import (
    Mem0SharedMemoryBackend,
    persona_mem0_user_id,
)
from hahobot.agent.memory_shared_sqlite import SharedMemorySQLiteState
from hahobot.config.schema import SharedMemoryConfig


def _config(**overrides: Any) -> SharedMemoryConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "baseUrl": "https://mem0.internal:8888",
        "apiKey": "secret-key",
        "userId": "shared-user",
        "agentId": "hahobot-test",
        "writeEnabled": True,
        "snapshotRefreshSeconds": 0,
    }
    values.update(overrides)
    return SharedMemoryConfig.model_validate(values)


def _event(event_id: str, *, kind: str = "memory_backfill") -> dict[str, Any]:
    return {
        "id": event_id,
        "created_at": "2026-07-28T00:00:00+00:00",
        "messages": [{"role": "user", "content": f"memory {event_id}"}],
        "metadata": {"event_kind": kind},
    }


def test_sqlite_backfill_receipt_is_atomic_and_force_requeues(tmp_path: Path) -> None:
    state = SharedMemorySQLiteState(tmp_path / "state")

    assert state.enqueue_backfill(_event("backfill-1")) == "enqueued"
    assert state.enqueue_backfill(_event("backfill-1")) == "pending"
    token, events = state.claim_due(force=True, limit=1, event_ids={"backfill-1"})
    assert [event["id"] for event in events] == ["backfill-1"]

    state.finish_claim(token, succeeded={"backfill-1"}, failed={})

    assert state.backfill_statuses({"backfill-1"}) == {"backfill-1": "delivered"}
    assert state.enqueue_backfill(_event("backfill-1")) == "delivered"
    assert state.enqueue_backfill(_event("backfill-1"), force=True) == "enqueued"
    assert state.backfill_statuses({"backfill-1"}) == {"backfill-1": "pending"}


@pytest.mark.asyncio
async def test_backend_drains_only_targets_and_records_safe_metadata(tmp_path: Path) -> None:
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    backend = Mem0SharedMemoryBackend(
        _config(projectId="HuaGCS/Hahobot", deviceId="workstation"),
        state_root=tmp_path / "state",
        transport=httpx.MockTransport(handler),
    )
    backend._state.enqueue(_event("ordinary-turn", kind="turn"))
    metadata = {
        "source_persona": "coder",
        "source_file": "memory/MEMORY.md",
        "memory_layer": "persona",
        "content_sha256": "abc123",
        "session_key": "must-not-leak",
        "workspace": "/private/workspace",
    }

    assert (
        await backend.enqueue_backfill(
            event_id="backfill-a",
            content="First imported memory",
            metadata=metadata,
        )
        == "enqueued"
    )
    assert (
        await backend.enqueue_backfill(
            event_id="backfill-b",
            content="Second imported memory",
            metadata=metadata,
        )
        == "enqueued"
    )

    statuses = await backend.drain_backfill({"backfill-a", "backfill-b"})

    assert statuses == {"backfill-a": "delivered", "backfill-b": "delivered"}
    assert len(payloads) == 2
    assert {payload["messages"][0]["content"] for payload in payloads} == {
        "First imported memory",
        "Second imported memory",
    }
    for payload in payloads:
        metadata_payload = payload["metadata"]
        assert metadata_payload["event_kind"] == "memory_backfill"
        assert metadata_payload["source_file"] == "memory/MEMORY.md"
        assert "session_key" not in metadata_payload
        assert "workspace" not in metadata_payload
    assert [event["id"] for event in backend._state.pending_events()] == ["ordinary-turn"]


@pytest.mark.asyncio
async def test_backend_offline_backfill_remains_pending(tmp_path: Path) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"error": "offline"})

    backend = Mem0SharedMemoryBackend(
        _config(),
        state_root=tmp_path / "state",
        transport=httpx.MockTransport(handler),
    )
    for event_id in ("backfill-a", "backfill-b"):
        await backend.enqueue_backfill(
            event_id=event_id,
            content=event_id,
            metadata={"source_file": "PROFILE.md"},
        )

    statuses = await backend.drain_backfill({"backfill-a", "backfill-b"})

    assert statuses == {"backfill-a": "pending", "backfill-b": "pending"}
    assert attempts == 1
    assert {event["attempts"] for event in backend._state.pending_events()} == {0, 1}


def test_persona_user_id_helper_matches_layered_namespace_rules() -> None:
    assert (
        persona_mem0_user_id(_config(personaUserIdPrefix="shared-private::"), "Coder")
        == "shared-private::coder"
    )
    with pytest.raises(ValueError, match="collides with the public userId"):
        persona_mem0_user_id(
            _config(userId="shared::coder", personaUserIdPrefix="shared"),
            "coder",
        )


@pytest.mark.asyncio
async def test_backfill_rejects_absolute_source_file(tmp_path: Path) -> None:
    backend = Mem0SharedMemoryBackend(_config(), state_root=tmp_path / "state")

    with pytest.raises(ValueError, match="relative logical path"):
        await backend.enqueue_backfill(
            event_id="backfill-a",
            content="safe memory",
            metadata={"source_file": "/private/workspace/PROFILE.md"},
        )
