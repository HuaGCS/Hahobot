"""Tests for the standard A2A (Agent2Agent) protocol adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from hahobot.a2a.models import (
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    STATE_CANCELED,
    STATE_COMPLETED,
    STATE_FAILED,
    TASK_NOT_CANCELABLE,
    TASK_NOT_FOUND,
    UNSUPPORTED_OPERATION,
    build_agent_card,
    extract_text_from_parts,
    make_message,
    make_task,
)
from hahobot.api.server import create_app

try:
    from aiohttp.test_utils import TestClient, TestServer

    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

pytest_plugins = ("pytest_asyncio",)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_agent(response_text: str = "hello from agent") -> MagicMock:
    agent = MagicMock()
    agent.process_direct = AsyncMock(return_value=response_text)
    agent._connect_mcp = AsyncMock()
    agent.close_mcp = AsyncMock()
    return agent


def _a2a_config(**overrides):
    base = {
        "enabled": True,
        "name": "hahobot",
        "description": "test bot",
        "version": "9.9.9",
        "public_url": "",
        "timeout": 5.0,
        "max_tasks": 3,
        "streaming": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_streaming_agent(deltas: list[str]) -> MagicMock:
    """Agent whose process_direct fires on_stream for each delta."""
    agent = MagicMock()

    async def _proc(
        content,
        *,
        session_key,
        channel,
        chat_id,
        on_stream=None,
        on_stream_end=None,
        on_progress=None,
    ):
        for delta in deltas:
            if on_stream is not None:
                await on_stream(delta)
        if on_stream_end is not None:
            await on_stream_end(resuming=False)
        return "".join(deltas)

    agent.process_direct = _proc
    agent._connect_mcp = AsyncMock()
    agent.close_mcp = AsyncMock()
    return agent


def _parse_sse(text: str) -> list[dict]:
    """Parse SSE 'data:' frames into JSON-RPC envelopes."""
    events = []
    for block in text.strip().split("\n\n"):
        block = block.strip()
        if block.startswith("data:"):
            events.append(json.loads(block[len("data:") :].strip()))
    return events


@pytest.fixture
def mock_agent():
    return _make_mock_agent()


@pytest.fixture
def app(mock_agent):
    return create_app(
        mock_agent,
        model_name="test-model",
        request_timeout=5.0,
        host="127.0.0.1",
        port=8900,
        a2a_config=_a2a_config(),
    )


@pytest_asyncio.fixture
async def aiohttp_client():
    clients: list = []

    async def _make_client(app):
        client = TestClient(TestServer(app))
        await client.start_server()
        clients.append(client)
        return client

    try:
        yield _make_client
    finally:
        for client in clients:
            await client.close()


# ---------------------------------------------------------------------------
# Pure model helpers (no server needed)
# ---------------------------------------------------------------------------


def test_build_agent_card_shape() -> None:
    card = build_agent_card(name="hahobot", description="d", url="http://h:1/a2a", version="1.2.3")
    assert card["name"] == "hahobot"
    assert card["url"] == "http://h:1/a2a"
    assert card["version"] == "1.2.3"
    assert card["protocolVersion"] == "0.3.0"
    assert card["preferredTransport"] == "JSONRPC"
    assert card["capabilities"]["streaming"] is False
    assert card["defaultInputModes"] == ["text/plain"]
    assert card["skills"][0]["id"] == "chat"


def test_extract_text_from_parts_variants() -> None:
    assert extract_text_from_parts([{"kind": "text", "text": "a"}]) == "a"
    # legacy "type" key and multi-part join
    assert (
        extract_text_from_parts([{"type": "text", "text": "a"}, {"kind": "text", "text": "b"}])
        == "a\nb"
    )
    # non-text parts ignored
    assert extract_text_from_parts([{"kind": "file"}, {"kind": "text", "text": "x"}]) == "x"


def test_extract_text_from_parts_empty_raises() -> None:
    with pytest.raises(ValueError):
        extract_text_from_parts([{"kind": "file"}])
    with pytest.raises(ValueError):
        extract_text_from_parts([{"kind": "text", "text": "   "}])


def test_make_message_and_task() -> None:
    msg = make_message(role="user", text="hi", message_id="m1", context_id="c1", task_id="t1")
    assert msg["kind"] == "message"
    assert msg["parts"][0] == {"kind": "text", "text": "hi"}
    assert msg["contextId"] == "c1" and msg["taskId"] == "t1"

    task = make_task(task_id="t1", context_id="c1", state=STATE_COMPLETED, agent_text="ok")
    assert task["id"] == "t1"
    assert task["kind"] == "task"
    assert task["status"]["state"] == STATE_COMPLETED
    assert task["artifacts"][0]["parts"][0]["text"] == "ok"


# ---------------------------------------------------------------------------
# create_app wiring
# ---------------------------------------------------------------------------


def test_a2a_routes_absent_when_disabled(mock_agent) -> None:
    app = create_app(mock_agent, a2a_config=_a2a_config(enabled=False))
    paths = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/a2a" not in paths
    assert "/.well-known/agent-card.json" not in paths


def test_a2a_routes_absent_when_no_config(mock_agent) -> None:
    app = create_app(mock_agent)
    paths = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/a2a" not in paths


# ---------------------------------------------------------------------------
# Agent Card endpoint
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_agent_card_endpoint(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
        resp = await client.get(path)
        assert resp.status == 200
        card = await resp.json()
        assert card["name"] == "hahobot"
        assert card["version"] == "9.9.9"
        # public_url empty -> derived from host/port
        assert card["url"] == "http://127.0.0.1:8900/a2a"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_agent_card_uses_public_url(aiohttp_client, mock_agent) -> None:
    app = create_app(
        mock_agent,
        a2a_config=_a2a_config(public_url="https://bot.example.com"),
    )
    client = await aiohttp_client(app)
    resp = await client.get("/.well-known/agent-card.json")
    card = await resp.json()
    assert card["url"] == "https://bot.example.com/a2a"


# ---------------------------------------------------------------------------
# message/send
# ---------------------------------------------------------------------------


def _rpc(method: str, params=None, req_id="1"):
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_send_completed(aiohttp_client, app, mock_agent) -> None:
    client = await aiohttp_client(app)
    resp = await client.post(
        "/a2a",
        json=_rpc(
            "message/send",
            {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "ping"}],
                    "messageId": "m-1",
                    "contextId": "ctx-1",
                }
            },
        ),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == "1"
    task = body["result"]
    assert task["kind"] == "task"
    assert task["contextId"] == "ctx-1"
    assert task["status"]["state"] == STATE_COMPLETED
    assert task["artifacts"][0]["parts"][0]["text"] == "hello from agent"
    # history has user echo + agent reply
    assert [m["role"] for m in task["history"]] == ["user", "agent"]
    # session_key is derived from the contextId
    mock_agent.process_direct.assert_awaited_once()
    _, kwargs = mock_agent.process_direct.call_args
    assert kwargs["session_key"] == "a2a:ctx-1"
    assert kwargs["channel"] == "a2a"
    assert kwargs["content"] == "ping"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_send_generates_context_id(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post(
        "/a2a",
        json=_rpc("message/send", {"message": {"parts": [{"kind": "text", "text": "x"}]}}),
    )
    task = (await resp.json())["result"]
    assert task["contextId"]  # non-empty generated id


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_send_empty_reply_uses_fallback(aiohttp_client, mock_agent) -> None:
    from hahobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

    mock_agent.process_direct = AsyncMock(return_value="")
    app = create_app(mock_agent, a2a_config=_a2a_config())
    client = await aiohttp_client(app)
    resp = await client.post(
        "/a2a",
        json=_rpc("message/send", {"message": {"parts": [{"kind": "text", "text": "x"}]}}),
    )
    task = (await resp.json())["result"]
    assert task["status"]["state"] == STATE_COMPLETED
    assert task["artifacts"][0]["parts"][0]["text"] == EMPTY_FINAL_RESPONSE_MESSAGE


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_send_agent_error_yields_failed_task(aiohttp_client, mock_agent) -> None:
    mock_agent.process_direct = AsyncMock(side_effect=RuntimeError("boom"))
    app = create_app(mock_agent, a2a_config=_a2a_config())
    client = await aiohttp_client(app)
    resp = await client.post(
        "/a2a",
        json=_rpc("message/send", {"message": {"parts": [{"kind": "text", "text": "x"}]}}),
    )
    assert resp.status == 200
    task = (await resp.json())["result"]
    assert task["status"]["state"] == STATE_FAILED


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_send_missing_parts_is_invalid_params(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post("/a2a", json=_rpc("message/send", {"message": {"role": "user"}}))
    body = await resp.json()
    assert body["error"]["code"] == JSONRPC_INVALID_PARAMS


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_send_no_text_is_invalid_params(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post(
        "/a2a",
        json=_rpc("message/send", {"message": {"parts": [{"kind": "file"}]}}),
    )
    body = await resp.json()
    assert body["error"]["code"] == JSONRPC_INVALID_PARAMS


# ---------------------------------------------------------------------------
# tasks/get and tasks/cancel
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_tasks_get_roundtrip(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    send = await client.post(
        "/a2a",
        json=_rpc("message/send", {"message": {"parts": [{"kind": "text", "text": "x"}]}}),
    )
    task_id = (await send.json())["result"]["id"]

    got = await client.post("/a2a", json=_rpc("tasks/get", {"id": task_id}))
    task = (await got.json())["result"]
    assert task["id"] == task_id


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_tasks_get_not_found(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post("/a2a", json=_rpc("tasks/get", {"id": "nope"}))
    body = await resp.json()
    assert body["error"]["code"] == TASK_NOT_FOUND


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_tasks_cancel_terminal_not_cancelable(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    send = await client.post(
        "/a2a",
        json=_rpc("message/send", {"message": {"parts": [{"kind": "text", "text": "x"}]}}),
    )
    task_id = (await send.json())["result"]["id"]  # state == completed (terminal)

    resp = await client.post("/a2a", json=_rpc("tasks/cancel", {"id": task_id}))
    body = await resp.json()
    assert body["error"]["code"] == TASK_NOT_CANCELABLE


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_tasks_cancel_non_terminal(aiohttp_client, app) -> None:
    """Directly seed a working task in the store, then cancel it."""
    from hahobot.a2a.server import A2A_TASKS_KEY

    client = await aiohttp_client(app)
    working = make_task(task_id="tw", context_id="c", state="working", agent_text="")
    app[A2A_TASKS_KEY]["tw"] = working

    resp = await client.post("/a2a", json=_rpc("tasks/cancel", {"id": "tw"}))
    task = (await resp.json())["result"]
    assert task["status"]["state"] == STATE_CANCELED


# ---------------------------------------------------------------------------
# JSON-RPC envelope errors
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_parse_error(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post("/a2a", data="not json", headers={"Content-Type": "application/json"})
    body = await resp.json()
    assert body["error"]["code"] == JSONRPC_PARSE_ERROR


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_invalid_request_envelope(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post("/a2a", json={"method": "message/send"})  # no jsonrpc
    body = await resp.json()
    assert body["error"]["code"] == JSONRPC_INVALID_REQUEST


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_method_not_found(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post("/a2a", json=_rpc("foo/bar"))
    body = await resp.json()
    assert body["error"]["code"] == JSONRPC_METHOD_NOT_FOUND


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_stream_unsupported_when_disabled(aiohttp_client, mock_agent) -> None:
    app = create_app(mock_agent, a2a_config=_a2a_config(streaming=False))
    client = await aiohttp_client(app)
    resp = await client.post("/a2a", json=_rpc("message/stream", {"message": {}}))
    body = await resp.json()
    assert body["error"]["code"] == UNSUPPORTED_OPERATION


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_agent_card_advertises_streaming(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.get("/.well-known/agent-card.json")
    card = await resp.json()
    assert card["capabilities"]["streaming"] is True


# ---------------------------------------------------------------------------
# message/stream — SSE
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_stream_emits_sse_events(aiohttp_client) -> None:
    agent = _make_streaming_agent(["Hello", " world"])
    app = create_app(agent, a2a_config=_a2a_config())
    client = await aiohttp_client(app)

    resp = await client.post(
        "/a2a",
        json=_rpc(
            "message/stream",
            {"message": {"parts": [{"kind": "text", "text": "hi"}], "contextId": "c-s"}},
        ),
    )
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("text/event-stream")

    events = [e["result"] for e in _parse_sse(await resp.text())]
    kinds = [e["kind"] for e in events]
    # initial task, two artifact-update deltas, final status-update
    assert kinds[0] == "task"
    assert events[0]["status"]["state"] == "working"
    assert kinds.count("artifact-update") == 2
    assert kinds[-1] == "status-update"
    assert events[-1]["final"] is True
    assert events[-1]["status"]["state"] == STATE_COMPLETED

    # first artifact chunk append=False, second append=True
    artifacts = [e for e in events if e["kind"] == "artifact-update"]
    assert artifacts[0]["append"] is False
    assert artifacts[1]["append"] is True
    # reconstructed text
    text = "".join(a["artifact"]["parts"][0]["text"] for a in artifacts)
    assert text == "Hello world"

    # task is retrievable afterwards
    task_id = events[0]["id"]
    got = await client.post("/a2a", json=_rpc("tasks/get", {"id": task_id}))
    stored = (await got.json())["result"]
    assert stored["status"]["state"] == STATE_COMPLETED
    assert stored["artifacts"][0]["parts"][0]["text"] == "Hello world"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_stream_no_chunks_sends_full_text(aiohttp_client) -> None:
    # Agent that returns text without ever calling on_stream.
    agent = _make_streaming_agent([])
    agent.process_direct = AsyncMock(return_value="whole answer")
    app = create_app(agent, a2a_config=_a2a_config())
    client = await aiohttp_client(app)

    resp = await client.post(
        "/a2a",
        json=_rpc("message/stream", {"message": {"parts": [{"kind": "text", "text": "hi"}]}}),
    )
    events = [e["result"] for e in _parse_sse(await resp.text())]
    artifacts = [e for e in events if e["kind"] == "artifact-update"]
    assert len(artifacts) == 1
    assert artifacts[0]["lastChunk"] is True
    assert artifacts[0]["artifact"]["parts"][0]["text"] == "whole answer"
    assert events[-1]["status"]["state"] == STATE_COMPLETED


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_stream_validation_error_is_plain_jsonrpc(aiohttp_client, app) -> None:
    # No parts → validation fails BEFORE the SSE stream starts → plain JSON-RPC error.
    client = await aiohttp_client(app)
    resp = await client.post("/a2a", json=_rpc("message/stream", {"message": {"role": "user"}}))
    assert resp.headers["Content-Type"].startswith("application/json")
    body = await resp.json()
    assert body["error"]["code"] == JSONRPC_INVALID_PARAMS


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_message_stream_agent_error_emits_failed_status(aiohttp_client) -> None:
    agent = MagicMock()
    agent.process_direct = AsyncMock(side_effect=RuntimeError("boom"))
    agent._connect_mcp = AsyncMock()
    app = create_app(agent, a2a_config=_a2a_config())
    client = await aiohttp_client(app)

    resp = await client.post(
        "/a2a",
        json=_rpc("message/stream", {"message": {"parts": [{"kind": "text", "text": "hi"}]}}),
    )
    events = [e["result"] for e in _parse_sse(await resp.text())]
    assert events[-1]["kind"] == "status-update"
    assert events[-1]["final"] is True
    assert events[-1]["status"]["state"] == STATE_FAILED


# ---------------------------------------------------------------------------
# Bounded task store eviction
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_task_store_eviction(aiohttp_client, app) -> None:
    from hahobot.a2a.server import A2A_TASKS_KEY

    client = await aiohttp_client(app)
    # max_tasks == 3; send 5 messages.
    for _ in range(5):
        await client.post(
            "/a2a",
            json=_rpc("message/send", {"message": {"parts": [{"kind": "text", "text": "x"}]}}),
        )
    assert len(app[A2A_TASKS_KEY]) == 3


def test_jsonrpc_error_helper_is_http_200() -> None:
    from hahobot.a2a.server import _jsonrpc_error

    resp = _jsonrpc_error("1", TASK_NOT_FOUND, "x")
    assert resp.status == 200
    assert json.loads(resp.body)["error"]["code"] == TASK_NOT_FOUND
