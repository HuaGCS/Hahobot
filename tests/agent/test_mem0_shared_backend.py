"""Tests for the additive Mem0 shared-memory backend."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hahobot.agent.memory_backends.mem0_backend import (
    LayeredMem0SharedMemoryBackend,
    Mem0SharedMemoryBackend,
)
from hahobot.agent.memory_models import MemoryCommitRequest, MemoryScope
from hahobot.agent.memory_shared_sqlite import SharedMemorySQLiteState
from hahobot.config.schema import SharedMemoryConfig


def _config(**overrides: Any) -> SharedMemoryConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "baseUrl": "https://mem0.internal:8888",
        "apiKey": "secret-key",
        "userId": "hua-global-v1",
        "agentId": "hahobot-workstation",
        "projectId": "HuaGCS/Hahobot",
        "deviceId": "workstation",
        "globalWriteMode": "full",
        "snapshotRefreshSeconds": 0,
    }
    values.update(overrides)
    return SharedMemoryConfig.model_validate(values)


def _scope(tmp_path: Path, *, query: str = "tea preference") -> MemoryScope:
    return MemoryScope(
        workspace=tmp_path / "workspace",
        session_key="cli:direct",
        channel="cli",
        chat_id="direct",
        sender_id="channel-user-42",
        persona="coder",
        language="zh",
        query=query,
    )


def _request(tmp_path: Path, *, text: str = "A durable decision") -> MemoryCommitRequest:
    return MemoryCommitRequest(
        scope=_scope(tmp_path),
        inbound_content=text,
        outbound_content="Noted",
    )


def _scheduler(tasks: list[asyncio.Task[Any]]):
    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    return schedule


@pytest.mark.asyncio
async def test_mem0_search_uses_canonical_user_scope_and_api_key(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"results": [{"id": "m1", "memory": "Prefers green tea"}]},
        )

    backend = Mem0SharedMemoryBackend(
        _config(writeEnabled=False),
        state_root=tmp_path / "instance-state",
        transport=httpx.MockTransport(handler),
    )
    resolved = await backend.resolve_context(
        _scope(
            tmp_path,
            query="tea preference <private>query-secret</private>\nprivate: hidden-query",
        )
    )

    assert resolved.source == "mem0"
    assert resolved.block == "- Prefers green tea"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/search"
    assert request.headers["X-API-Key"] == "secret-key"
    assert json.loads(request.content) == {
        "query": "tea preference [private redacted]",
        "top_k": 8,
        "filters": {"user_id": "hua-global-v1"},
    }
    assert b"query-secret" not in request.content
    assert b"hidden-query" not in request.content


@pytest.mark.asyncio
async def test_private_only_query_never_leaves_the_device(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    backend = Mem0SharedMemoryBackend(
        _config(writeEnabled=False, snapshotRefreshSeconds=0),
        state_root=tmp_path / "instance-state",
        transport=httpx.MockTransport(handler),
    )
    await backend.resolve_context(_scope(tmp_path, query="private: never-send-this-query"))

    assert requests == []


@pytest.mark.asyncio
async def test_persona_private_query_skips_public_namespace(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    backend = Mem0SharedMemoryBackend(
        _config(writeEnabled=False),
        state_root=tmp_path / "instance-state",
        transport=httpx.MockTransport(handler),
    )
    await backend.resolve_context(
        _scope(
            tmp_path,
            query="<persona-private>call me captain</persona-private>",
        )
    )

    assert requests == []


@pytest.mark.asyncio
async def test_unclosed_private_markers_never_leave_public_namespace(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    backend = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=tmp_path / "instance-state",
        transport=httpx.MockTransport(handler),
    )
    backend.retire()
    await backend.commit_turn(
        MemoryCommitRequest(
            scope=_scope(tmp_path),
            inbound_content="visible <persona-private>never public",
            outbound_content="visible <private>never persist",
        )
    )

    queued = await asyncio.to_thread(backend._state.pending_events)
    assert queued[0]["messages"] == [
        {"role": "user", "content": "visible"},
        {"role": "assistant", "content": "visible [private redacted]"},
    ]
    assert "never public" not in json.dumps(queued)
    assert "never persist" not in json.dumps(queued)
    assert requests == []


@pytest.mark.asyncio
async def test_mem0_search_strips_remote_private_content_before_cache_and_prompt(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "m1",
                        "memory": (
                            "Uses Ruff\n<private>remote-secret</private>\n"
                            "private: another-secret\n"
                            "<persona-private>wrongly-public-secret</persona-private>"
                        ),
                    }
                ]
            },
        )

    backend = Mem0SharedMemoryBackend(
        _config(writeEnabled=False),
        state_root=tmp_path / "instance-state",
        transport=httpx.MockTransport(handler),
    )
    resolved = await backend.resolve_context(_scope(tmp_path, query="Ruff"))
    cached = await asyncio.to_thread(backend._state.snapshot_items)

    assert "Uses Ruff" in resolved.block
    assert "remote-secret" not in resolved.block
    assert "another-secret" not in resolved.block
    assert "wrongly-public-secret" not in resolved.block
    assert "remote-secret" not in json.dumps(cached)
    assert "another-secret" not in json.dumps(cached)
    assert "wrongly-public-secret" not in json.dumps(cached)


@pytest.mark.asyncio
async def test_mem0_malformed_search_falls_back_to_local_snapshot(tmp_path: Path) -> None:
    async def success(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"id": "m1", "memory": "Uses Ruff for linting"}]},
        )

    state_root = tmp_path / "instance-state"
    scope = _scope(tmp_path, query="Ruff")
    warm_backend = Mem0SharedMemoryBackend(
        _config(writeEnabled=False),
        state_root=state_root,
        transport=httpx.MockTransport(success),
    )
    assert "Uses Ruff" in (await warm_backend.resolve_context(scope)).block

    async def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": []})

    fallback_backend = Mem0SharedMemoryBackend(
        _config(writeEnabled=False),
        state_root=state_root,
        transport=httpx.MockTransport(malformed),
    )
    resolved = await fallback_backend.resolve_context(scope)

    assert resolved.source == "mem0-cache"
    assert resolved.block == "- Uses Ruff for linting"
    assert not (scope.workspace / ".hahobot").exists()


@pytest.mark.asyncio
async def test_mem0_commit_is_durable_before_network_and_sanitized(tmp_path: Path) -> None:
    payloads: list[dict[str, Any]] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/memories"
        started.set()
        await release.wait()
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    tasks: list[asyncio.Task[Any]] = []
    backend = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=tmp_path / "instance-state",
        schedule_background=_scheduler(tasks),
        transport=httpx.MockTransport(handler),
    )
    await backend.commit_turn(
        MemoryCommitRequest(
            scope=_scope(tmp_path),
            inbound_content="Remember tea. <private>token-123</private>",
            outbound_content="Saved.\nprivate: hidden-line",
        )
    )
    await started.wait()

    queued = await asyncio.to_thread(backend._state.pending_events)
    assert len(queued) == 1
    queued_json = json.dumps(queued, ensure_ascii=False)
    assert "token-123" not in queued_json
    assert "hidden-line" not in queued_json

    release.set()
    await asyncio.gather(*tasks)

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["user_id"] == "hua-global-v1"
    assert payload["agent_id"] == "hahobot-workstation"
    assert payload["infer"] is True
    assert payload["messages"] == [
        {"role": "user", "content": "Remember tea. [private redacted]"},
        {"role": "assistant", "content": "Saved."},
    ]
    assert payload["metadata"]["project_id"] == "HuaGCS/Hahobot"
    assert payload["metadata"]["device_id"] == "workstation"
    assert payload["metadata"]["source_agent"] == "hahobot-workstation"
    assert "session_key" not in payload["metadata"]
    assert "channel" not in payload["metadata"]
    assert "chat_id" not in payload["metadata"]
    assert "sender_id" not in payload["metadata"]
    assert "channel-user-42" not in json.dumps(payload)
    assert await asyncio.to_thread(backend._state.pending_events) == []
    await backend.close()


@pytest.mark.asyncio
async def test_identical_turns_remain_distinct_and_keep_original_agent_id(tmp_path: Path) -> None:
    state_root = tmp_path / "instance-state"
    old_backend = Mem0SharedMemoryBackend(
        _config(readEnabled=False, agentId="old-hahobot"),
        state_root=state_root,
    )
    old_backend.retire()
    request = _request(tmp_path, text="same content")
    await old_backend.commit_turn(request)
    await old_backend.commit_turn(request)

    queued = await asyncio.to_thread(old_backend._state.pending_events)
    assert len(queued) == 2
    assert len({item["id"] for item in queued}) == 2

    payloads: list[dict[str, Any]] = []

    async def handler(http_request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(http_request.content))
        return httpx.Response(200, json={"results": []})

    rotated_backend = Mem0SharedMemoryBackend(
        _config(readEnabled=False, agentId="new-hahobot", apiKey="rotated"),
        state_root=state_root,
        transport=httpx.MockTransport(handler),
    )
    await rotated_backend.flush_session(_scope(tmp_path))
    await rotated_backend.flush_session(_scope(tmp_path))

    assert len(payloads) == 2
    assert {payload["agent_id"] for payload in payloads} == {"old-hahobot"}
    assert await asyncio.to_thread(rotated_backend._state.pending_events) == []
    await rotated_backend.close()


@pytest.mark.asyncio
async def test_mem0_failed_write_survives_and_force_flush_retries(tmp_path: Path) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"detail": "offline"})
        return httpx.Response(200, json={"results": []})

    tasks: list[asyncio.Task[Any]] = []
    backend = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=tmp_path / "instance-state",
        schedule_background=_scheduler(tasks),
        transport=httpx.MockTransport(handler),
    )
    await backend.commit_turn(_request(tmp_path))
    await asyncio.gather(*tasks)

    queued = await asyncio.to_thread(backend._state.pending_events)
    assert len(queued) == 1
    assert queued[0]["attempts"] == 1

    await backend.flush_session(_scope(tmp_path))

    assert attempts == 2
    assert await asyncio.to_thread(backend._state.pending_events) == []
    await backend.close()


@pytest.mark.asyncio
async def test_mem0_full_snapshot_refresh_supports_offline_recall(tmp_path: Path) -> None:
    tasks: list[asyncio.Task[Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": []})
        assert request.url.path == "/memories"
        assert request.url.params["user_id"] == "hua-global-v1"
        assert request.url.params["top_k"] == "1000"
        return httpx.Response(
            200,
            json={"results": [{"id": "remote-1", "memory": "NAS-wide shared fact"}]},
        )

    backend = Mem0SharedMemoryBackend(
        _config(writeEnabled=False, snapshotRefreshSeconds=1),
        state_root=tmp_path / "instance-state",
        schedule_background=_scheduler(tasks),
        transport=httpx.MockTransport(handler),
    )
    await backend.resolve_context(_scope(tmp_path, query="unrelated"))
    await asyncio.gather(*tasks)

    snapshot = await asyncio.to_thread(backend._state.snapshot_items)
    assert snapshot == [
        {
            "id": "remote-1",
            "memory": "NAS-wide shared fact",
            "created_at": "",
            "updated_at": "",
        }
    ]


@pytest.mark.asyncio
async def test_full_snapshot_at_server_limit_does_not_prune_cached_tail(
    tmp_path: Path,
) -> None:
    tasks: list[asyncio.Task[Any]] = []
    remote = [{"id": f"remote-{index}", "memory": f"remote fact {index}"} for index in range(1_000)]

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"results": remote})

    backend = Mem0SharedMemoryBackend(
        _config(writeEnabled=False, snapshotRefreshSeconds=1),
        state_root=tmp_path / "instance-state",
        schedule_background=_scheduler(tasks),
        transport=httpx.MockTransport(handler),
    )
    await asyncio.to_thread(
        backend._state.merge_snapshot,
        [{"id": "cached-tail", "memory": "possibly truncated server result"}],
    )

    await backend.resolve_context(_scope(tmp_path))
    await asyncio.gather(*tasks)

    snapshot = await asyncio.to_thread(backend._state.snapshot_items)
    assert len(snapshot) == 1_001
    assert any(item["id"] == "cached-tail" for item in snapshot)


@pytest.mark.asyncio
async def test_malformed_full_refresh_preserves_existing_snapshot(tmp_path: Path) -> None:
    tasks: list[asyncio.Task[Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"not_memories": []})

    backend = Mem0SharedMemoryBackend(
        _config(writeEnabled=False, snapshotRefreshSeconds=1),
        state_root=tmp_path / "instance-state",
        schedule_background=_scheduler(tasks),
        transport=httpx.MockTransport(handler),
    )
    await asyncio.to_thread(
        backend._state.merge_snapshot,
        [{"id": "keep", "memory": "keep this cached fact"}],
    )
    await backend.resolve_context(_scope(tmp_path))
    await asyncio.gather(*tasks)

    snapshot = await asyncio.to_thread(backend._state.snapshot_items)
    assert [item["memory"] for item in snapshot] == ["keep this cached fact"]


def test_full_refresh_cannot_overwrite_newer_search_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter((100.0, 200.0, 300.0))
    monkeypatch.setattr("hahobot.agent.memory_shared_sqlite.time.time", lambda: next(clock))
    state = SharedMemorySQLiteState(tmp_path / "shared-state")
    claim = state.claim_snapshot_refresh(1)
    assert claim is not None
    token, started_at = claim

    state.merge_snapshot(
        [{"id": "same-id", "memory": "new search value", "updated_at": "2026-07-28"}]
    )
    assert state.complete_snapshot_refresh(
        token,
        started_at,
        [{"id": "same-id", "memory": "stale full GET value", "updated_at": "2020-01-01"}],
    )

    assert state.snapshot_items()[0]["memory"] == "new search value"


@pytest.mark.asyncio
async def test_mem0_skips_non_user_background_turns(tmp_path: Path) -> None:
    backend = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=tmp_path / "instance-state",
    )

    await backend.commit_turn(
        MemoryCommitRequest(
            scope=_scope(tmp_path),
            inbound_content=None,
            outbound_content="cron result",
        )
    )

    assert await asyncio.to_thread(backend._state.pending_events) == []
    await backend.close()


def test_state_scope_is_service_and_user_not_project_or_api_key(tmp_path: Path) -> None:
    state_root = tmp_path / "instance-state"
    first = Mem0SharedMemoryBackend(_config(), state_root=state_root)
    same_user = Mem0SharedMemoryBackend(
        _config(apiKey="rotated", projectId="another-project", deviceId="nas"),
        state_root=state_root,
    )
    different_user = Mem0SharedMemoryBackend(
        _config(userId="another-user"),
        state_root=state_root,
    )

    assert first.state_path == same_user.state_path
    assert first.state_path != different_user.state_path


@pytest.mark.asyncio
async def test_public_write_policy_applies_without_persona_mode(tmp_path: Path) -> None:
    user_only = Mem0SharedMemoryBackend(
        _config(readEnabled=False, globalWriteMode="user_only"),
        state_root=tmp_path / "user-only-state",
    )
    user_only.retire()
    await user_only.commit_turn(_request(tmp_path))

    queued = await asyncio.to_thread(user_only._state.pending_events)
    assert queued[0]["messages"] == [{"role": "user", "content": "A durable decision"}]

    read_only = Mem0SharedMemoryBackend(
        _config(readEnabled=False, globalWriteMode="off"),
        state_root=tmp_path / "read-only-state",
    )
    await read_only.commit_turn(_request(tmp_path))

    assert await asyncio.to_thread(read_only._state.pending_events) == []


def test_shared_mem0_defaults_public_writes_to_user_only() -> None:
    assert SharedMemoryConfig().global_write_mode == "user_only"


@pytest.mark.asyncio
async def test_layered_mem0_recalls_public_and_current_persona_namespaces(
    tmp_path: Path,
) -> None:
    searched_user_ids: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        user_id = payload["filters"]["user_id"]
        searched_user_ids.append(user_id)
        return httpx.Response(
            200,
            json={"results": [{"id": user_id, "memory": f"fact from {user_id}"}]},
        )

    backend = LayeredMem0SharedMemoryBackend(
        _config(
            writeEnabled=False,
            personaEnabled=True,
            personaUserIdPrefix="hua-private",
            globalWriteMode="user_only",
        ),
        state_root=tmp_path / "instance-state",
        persona_names=lambda: ["default", "coder"],
        transport=httpx.MockTransport(handler),
    )
    resolved = await backend.resolve_context(_scope(tmp_path, query="fact"))

    assert set(searched_user_ids) == {"hua-global-v1", "hua-private::coder"}
    assert "[Public shared facts]" in resolved.block
    assert "fact from hua-global-v1" in resolved.block
    assert "[Private facts for persona coder]" in resolved.block
    assert "fact from hua-private::coder" in resolved.block
    assert "hua-private::default" not in resolved.block


def test_layered_mem0_rejects_public_private_user_id_collision(tmp_path: Path) -> None:
    backend = LayeredMem0SharedMemoryBackend(
        _config(
            userId="SHARED::coder",
            personaEnabled=True,
            personaUserIdPrefix="shared",
        ),
        state_root=tmp_path / "instance-state",
        persona_names=lambda: ["coder"],
    )

    with pytest.raises(ValueError, match="collides with the public userId"):
        backend.persona_user_id("coder")
    assert backend.persona_backends == {}


@pytest.mark.asyncio
async def test_layered_persona_private_query_reaches_only_current_persona(
    tmp_path: Path,
) -> None:
    searches: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        searches.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    backend = LayeredMem0SharedMemoryBackend(
        _config(
            writeEnabled=False,
            personaEnabled=True,
            personaUserIdPrefix="hua-private",
        ),
        state_root=tmp_path / "instance-state",
        persona_names=lambda: ["coder"],
        transport=httpx.MockTransport(handler),
    )
    await backend.resolve_context(
        _scope(
            tmp_path,
            query="<persona-private>call me captain</persona-private>",
        )
    )

    assert searches == [
        {
            "query": "call me captain",
            "top_k": 8,
            "filters": {"user_id": "hua-private::coder"},
        }
    ]


@pytest.mark.asyncio
async def test_persona_namespace_unwraps_remote_persona_private_memory(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        user_id = json.loads(request.content)["filters"]["user_id"]
        if user_id == "hua-private::coder":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "persona-fact",
                            "memory": ("<persona-private>Call the user captain</persona-private>"),
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    backend = LayeredMem0SharedMemoryBackend(
        _config(
            writeEnabled=False,
            personaEnabled=True,
            personaUserIdPrefix="hua-private",
        ),
        state_root=tmp_path / "instance-state",
        persona_names=lambda: ["coder"],
        transport=httpx.MockTransport(handler),
    )
    resolved = await backend.resolve_context(_scope(tmp_path, query="captain"))

    assert "Call the user captain" in resolved.block
    assert "persona-private" not in resolved.block


@pytest.mark.asyncio
async def test_layered_mem0_writes_user_only_public_and_full_turn_private(
    tmp_path: Path,
) -> None:
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    tasks: list[asyncio.Task[Any]] = []
    backend = LayeredMem0SharedMemoryBackend(
        _config(
            readEnabled=False,
            personaEnabled=True,
            personaUserIdPrefix="hua-private",
            globalWriteMode="user_only",
        ),
        state_root=tmp_path / "instance-state",
        persona_names=lambda: ["default", "coder"],
        schedule_background=_scheduler(tasks),
        transport=httpx.MockTransport(handler),
    )
    await backend.commit_turn(
        MemoryCommitRequest(
            scope=_scope(tmp_path),
            inbound_content="I prefer tea",
            outbound_content="Coder persona acknowledges this preference",
        )
    )
    await asyncio.gather(*tasks)

    by_user = {payload["user_id"]: payload for payload in payloads}
    assert set(by_user) == {"hua-global-v1", "hua-private::coder"}
    assert by_user["hua-global-v1"]["messages"] == [{"role": "user", "content": "I prefer tea"}]
    assert by_user["hua-global-v1"]["metadata"]["memory_namespace"] == "global"
    assert by_user["hua-private::coder"]["messages"] == [
        {"role": "user", "content": "I prefer tea"},
        {
            "role": "assistant",
            "content": "Coder persona acknowledges this preference",
        },
    ]
    assert by_user["hua-private::coder"]["metadata"]["memory_namespace"] == "persona"
    await backend.close()


@pytest.mark.asyncio
async def test_layered_mem0_routes_persona_private_marker_only_to_persona(
    tmp_path: Path,
) -> None:
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    tasks: list[asyncio.Task[Any]] = []
    backend = LayeredMem0SharedMemoryBackend(
        _config(
            readEnabled=False,
            personaEnabled=True,
            personaUserIdPrefix="hua-private",
            globalWriteMode="user_only",
        ),
        state_root=tmp_path / "instance-state",
        persona_names=lambda: ["coder"],
        schedule_background=_scheduler(tasks),
        transport=httpx.MockTransport(handler),
    )
    await backend.commit_turn(
        MemoryCommitRequest(
            scope=_scope(tmp_path),
            inbound_content=(
                "<persona-private>Only coder should call me captain</persona-private>"
            ),
            outbound_content="Understood, captain",
        )
    )
    await asyncio.gather(*tasks)

    assert [payload["user_id"] for payload in payloads] == ["hua-private::coder"]
    assert payloads[0]["messages"] == [
        {"role": "user", "content": "Only coder should call me captain"},
        {"role": "assistant", "content": "Understood, captain"},
    ]
    assert "persona-private" not in json.dumps(payloads)
    await backend.close()
