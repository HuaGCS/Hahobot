"""Lifecycle and concurrency regressions for the shared Mem0 memory layer."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from hahobot.agent.memory_backends.file_backend import FileUserMemoryBackend
from hahobot.agent.memory_backends.mem0_backend import Mem0SharedMemoryBackend
from hahobot.agent.memory_models import MemoryCommitRequest, MemoryScope
from hahobot.agent.memory_router import MemoryRouter
from hahobot.agent.memory_shared_sqlite import _connect
from hahobot.bus.queue import MessageBus
from hahobot.config.schema import Config, SharedMemoryConfig
from hahobot.providers.base import GenerationSettings


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


def _event(event_id: str = "event-1") -> dict[str, Any]:
    return {
        "id": event_id,
        "created_at": "2026-07-28T00:00:00+00:00",
        "attempts": 0,
        "next_attempt_at": 0.0,
        "messages": [{"role": "user", "content": "Remember the NAS decision"}],
        "metadata": {"source_agent": "hahobot-workstation"},
    }


def _scope(tmp_path: Path, *, query: str = "") -> MemoryScope:
    return MemoryScope(
        workspace=tmp_path / "workspace",
        session_key="cli:direct",
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        persona="coder",
        language="en",
        query=query,
    )


async def _wait_until_retired(
    backend: Mem0SharedMemoryBackend,
    *,
    timeout: float = 1.0,
) -> None:
    async def wait() -> None:
        while not backend._retired:
            task = backend._drain_task
            if task is None:
                await asyncio.sleep(0)
                continue
            await asyncio.gather(task, return_exceptions=True)

    await asyncio.wait_for(wait(), timeout=timeout)


def test_start_recovers_persisted_outbox_after_sync_construction(tmp_path: Path) -> None:
    posts: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(200, json={"results": []})

    # Construction and SQLite staging deliberately happen without a running loop.
    backend = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=tmp_path / "state",
        transport=httpx.MockTransport(handler),
    )
    backend._state.enqueue(_event())

    async def run() -> None:
        await backend.start()
        drain = backend._drain_task
        assert drain is not None
        await drain
        await backend.close()

    asyncio.run(run())

    assert [request.url.path for request in posts] == ["/memories"]
    assert backend._state.pending_events() == []


@pytest.mark.asyncio
async def test_two_backends_claim_same_outbox_event_only_once(tmp_path: Path) -> None:
    request_started = asyncio.Event()
    release_request = asyncio.Event()
    post_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        post_count += 1
        request_started.set()
        await release_request.wait()
        return httpx.Response(200, json={"results": []})

    state_root = tmp_path / "state"
    transport = httpx.MockTransport(handler)
    first = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=state_root,
        transport=transport,
    )
    second = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=state_root,
        transport=transport,
    )
    first._state.enqueue(_event())

    await asyncio.gather(first.start(), second.start())
    first_drain = first._drain_task
    second_drain = second._drain_task
    assert first_drain is not None
    assert second_drain is not None
    await asyncio.wait_for(request_started.wait(), timeout=1)
    release_request.set()
    await asyncio.gather(first_drain, second_drain)

    assert post_count == 1
    assert first._state.pending_events() == []
    await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_retire_cancels_backoff_and_close_is_idempotent(tmp_path: Path) -> None:
    request_seen = asyncio.Event()
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        request_seen.set()
        return httpx.Response(503, json={"detail": "offline"})

    backend = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=tmp_path / "state",
        transport=httpx.MockTransport(handler),
    )
    backend._state.enqueue(_event())

    await backend.start()
    drain = backend._drain_task
    assert drain is not None
    await asyncio.wait_for(request_seen.wait(), timeout=1)
    await drain

    retry_handle = backend._retry_handle
    assert retry_handle is not None
    assert not retry_handle.cancelled()
    queued = backend._state.pending_events()
    assert len(queued) == 1
    assert queued[0]["attempts"] == 1

    backend.retire()
    assert retry_handle.cancelled()
    assert backend._retry_handle is None
    backend._retry_wakeup()
    await asyncio.sleep(0)
    assert request_count == 1
    assert len(backend._state.pending_events()) == 1

    await backend.close()
    await backend.close()
    assert request_count == 1
    assert len(backend._state.pending_events()) == 1


@pytest.mark.asyncio
async def test_runtime_reload_reuses_router_until_memory_config_changes(tmp_path: Path) -> None:
    from hahobot.agent.loop import AgentLoop

    workspace = tmp_path / "workspace"
    config_path = tmp_path / "config.json"
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.memory.shared = _config(readEnabled=False)

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings(max_tokens=1_024)

    with patch("hahobot.agent.loop.SubagentManager"):
        loop = AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=workspace,
            config_path=config_path,
            memory_config=config.memory,
        )

    original_router = loop.memory_router
    original_shared = original_router.shadow_backends[0]

    non_memory_update = config.model_copy(deep=True)
    non_memory_update.agents.defaults.temperature = 0.2
    await loop.reload_runtime_config(non_memory_update)

    assert loop.memory_router is original_router
    assert original_shared._started is True
    assert original_shared._retired is False

    memory_update = non_memory_update.model_copy(deep=True)
    memory_update.memory.shared.top_k += 1
    await loop.reload_runtime_config(memory_update)

    replacement_router = loop.memory_router
    replacement_shared = replacement_router.shadow_backends[0]
    assert replacement_router is not original_router
    assert replacement_shared is not original_shared
    await _wait_until_retired(original_shared)
    assert original_shared._retired is True
    assert replacement_shared._started is True
    assert replacement_shared._retired is False

    await loop.close_mcp()


@pytest.mark.asyncio
async def test_close_cancels_refresh_and_releases_snapshot_claim(tmp_path: Path) -> None:
    refresh_started = asyncio.Event()
    release_request = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/memories"
        refresh_started.set()
        await release_request.wait()
        return httpx.Response(200, json={"results": []})

    backend = Mem0SharedMemoryBackend(
        _config(writeEnabled=False, snapshotRefreshSeconds=3_600),
        state_root=tmp_path / "state",
        transport=httpx.MockTransport(handler),
    )

    await backend.resolve_context(_scope(tmp_path))
    refresh = backend._snapshot_task
    assert refresh is not None
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    assert backend._state.claim_snapshot_refresh(3_600) is None

    await backend.close()
    assert refresh.done()

    replacement_claim = backend._state.claim_snapshot_refresh(3_600)
    assert replacement_claim is not None
    backend._state.abort_snapshot_refresh(replacement_claim[0])
    release_request.set()


@pytest.mark.asyncio
async def test_cancel_during_sqlite_claim_waits_then_releases_token(tmp_path: Path) -> None:
    backend = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=tmp_path / "state",
    )
    backend._state.enqueue(_event())
    original_claim = backend._state.claim_due
    entered = threading.Event()
    release = threading.Event()

    def blocked_claim(*, force: bool, limit: int):
        entered.set()
        assert release.wait(timeout=1)
        return original_claim(force=force, limit=limit)

    backend._state.claim_due = blocked_claim  # type: ignore[method-assign]
    await backend.start()
    assert await asyncio.to_thread(entered.wait, 1)

    close_task = asyncio.create_task(backend.close())
    release.set()
    await close_task

    backend._state.claim_due = original_claim  # type: ignore[method-assign]
    token, events = backend._state.claim_due(force=True, limit=1)
    assert [event["id"] for event in events] == ["event-1"]
    backend._state.release_claim(token)


@pytest.mark.asyncio
async def test_retired_router_delivers_all_final_leased_turns_once(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            started.set()
            await release.wait()
        return httpx.Response(200, json={"results": []})

    tasks: list[asyncio.Task[Any]] = []

    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    shared = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=tmp_path / "state",
        schedule_background=schedule,
        transport=httpx.MockTransport(handler),
    )
    router = MemoryRouter(
        user_backend=FileUserMemoryBackend(),
        shadow_backends=[shared],
    )
    router.acquire_turn()
    router.acquire_turn()
    router.request_retirement()
    assert shared._retired is False

    first = MemoryCommitRequest(
        scope=_scope(tmp_path),
        inbound_content="first old-generation turn",
        outbound_content="one",
    )
    second = MemoryCommitRequest(
        scope=_scope(tmp_path),
        inbound_content="second old-generation turn",
        outbound_content="two",
    )
    await router.commit_turn(first)
    await asyncio.wait_for(started.wait(), timeout=1)
    router.release_turn()
    await router.commit_turn(second)
    router.release_turn()
    assert router._retired is True

    release.set()
    await _wait_until_retired(shared)
    await asyncio.gather(*tasks)

    assert [payload["messages"][0]["content"] for payload in payloads] == [
        "first old-generation turn",
        "second old-generation turn",
    ]
    assert shared._retired is True
    assert shared._state.pending_events() == []


@pytest.mark.asyncio
async def test_retirement_rechecks_outbox_after_running_empty_drain(
    tmp_path: Path,
) -> None:
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    tasks: list[asyncio.Task[Any]] = []

    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    shared = Mem0SharedMemoryBackend(
        _config(readEnabled=False),
        state_root=tmp_path / "state",
        schedule_background=schedule,
        transport=httpx.MockTransport(handler),
    )
    original_claim = shared._state.claim_due
    empty_claim_seen = threading.Event()
    allow_empty_claim_return = threading.Event()
    first_claim = True

    def blocked_first_empty_claim(*, force: bool, limit: int):
        nonlocal first_claim
        token, events = original_claim(force=force, limit=limit)
        if first_claim:
            first_claim = False
            assert events == []
            empty_claim_seen.set()
            assert allow_empty_claim_return.wait(timeout=1)
        return token, events

    shared._state.claim_due = blocked_first_empty_claim  # type: ignore[method-assign]
    router = MemoryRouter(
        user_backend=FileUserMemoryBackend(),
        shadow_backends=[shared],
    )
    router.acquire_turn()
    await shared.start()
    assert await asyncio.to_thread(empty_claim_seen.wait, 1)

    router.request_retirement()
    await router.commit_turn(
        MemoryCommitRequest(
            scope=_scope(tmp_path),
            inbound_content="committed after the empty claim",
            outbound_content="recorded",
        )
    )
    router.release_turn()
    allow_empty_claim_return.set()

    await _wait_until_retired(shared)
    await asyncio.gather(*tasks)

    assert [payload["messages"][0]["content"] for payload in payloads] == [
        "committed after the empty claim"
    ]
    assert shared._state.pending_events() == []


def test_shared_sqlite_connection_context_closes_descriptor(tmp_path: Path) -> None:
    with _connect(tmp_path / "state" / "shared.sqlite") as conn:
        conn.execute("CREATE TABLE sample(value TEXT)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_shared_sqlite_first_use_waits_for_journal_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "shared.sqlite"
    db_path.parent.mkdir(parents=True)
    setup = sqlite3.connect(db_path)
    setup.execute("CREATE TABLE sample(value TEXT)")
    setup.commit()
    setup.close()

    blocker = sqlite3.connect(db_path)
    blocker.execute("BEGIN EXCLUSIVE")
    entered = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def connect_while_locked() -> None:
        entered.set()
        try:
            with _connect(db_path) as conn:
                conn.execute("SELECT * FROM sample").fetchall()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=connect_while_locked)
    thread.start()
    assert entered.wait(timeout=1)
    assert not finished.wait(timeout=0.05)
    blocker.rollback()
    blocker.close()
    thread.join(timeout=1)

    assert finished.is_set()
    assert errors == []
