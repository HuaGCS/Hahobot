"""Tests for the Xiaoyi (华为小艺) A2A channel.

Connection logic is exercised through fake WebSocket objects so the suite
doesn't require a live Huawei endpoint. Auth/signature, inbound dispatch
(``message/stream`` / ``clearContext`` / ``tasks/cancel``), the outbound
A2A ``artifact-update`` envelope, and the session-level heartbeat are all
covered.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from hahobot.bus.events import OutboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.channels.registry import discover_all
from hahobot.channels.xiaoyi import (
    XiaoyiChannel,
    _generate_auth_headers,
    _generate_signature,
    _ssl_context_for_url,
)
from hahobot.config.schema import Config, XiaoyiConfig

# ---------------------------------------------------------------------------
# Discovery + schema
# ---------------------------------------------------------------------------


def test_xiaoyi_module_is_discoverable_via_registry() -> None:
    channels = discover_all()
    assert "xiaoyi" in channels
    assert channels["xiaoyi"] is XiaoyiChannel


def test_default_config_is_disabled_and_carries_jiuwenswarm_fields() -> None:
    cfg = XiaoyiConfig()
    assert cfg.enabled is False
    assert cfg.ak == ""
    assert cfg.sk == ""
    assert cfg.agent_id == ""
    assert cfg.ws_url1 == ""
    assert cfg.ws_url2 == ""
    assert cfg.enable_streaming is True
    assert cfg.allow_from == ["*"]


def test_top_level_config_carries_xiaoyi_block() -> None:
    assert Config().channels.xiaoyi.enabled is False


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def test_signature_matches_hmac_sha256_base64() -> None:
    sk = "shhh"
    ts = "1690000000000"
    expected = base64.b64encode(
        hmac.new(sk.encode(), ts.encode(), hashlib.sha256).digest()
    ).decode()
    assert _generate_signature(sk, ts) == expected


def test_auth_headers_carry_all_four_fields(monkeypatch) -> None:
    monkeypatch.setattr("hahobot.channels.xiaoyi.time.time", lambda: 1_700_000_000.123)
    headers = _generate_auth_headers("k", "s", "agent-9")
    assert headers["x-access-key"] == "k"
    assert headers["x-agent-id"] == "agent-9"
    assert headers["x-ts"] == "1700000000123"
    expected_sig = base64.b64encode(
        hmac.new(b"s", b"1700000000123", hashlib.sha256).digest()
    ).decode()
    assert headers["x-sign"] == expected_sig


def test_ssl_context_disables_hostname_check_for_ip_literals() -> None:
    ctx = _ssl_context_for_url("wss://10.0.0.1/foo")
    assert ctx.check_hostname is False
    ctx = _ssl_context_for_url("wss://xiaoyi.example.com/foo")
    assert ctx.check_hostname is True


# ---------------------------------------------------------------------------
# Lifecycle short-circuits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_is_noop_when_disabled() -> None:
    channel = XiaoyiChannel(XiaoyiConfig(), MessageBus())
    await channel.start()
    assert channel.is_running is False


@pytest.mark.asyncio
async def test_start_refuses_without_credentials() -> None:
    cfg = XiaoyiConfig(enabled=True, ws_url1="wss://x/", ws_url2="wss://y/")
    channel = XiaoyiChannel(cfg, MessageBus())
    await channel.start()
    assert channel.is_running is False


@pytest.mark.asyncio
async def test_start_refuses_without_any_ws_url() -> None:
    cfg = XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="a")
    channel = XiaoyiChannel(cfg, MessageBus())
    await channel.start()
    assert channel.is_running is False


# ---------------------------------------------------------------------------
# Inbound dispatch (no real ws needed)
# ---------------------------------------------------------------------------


class _CapturingWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


def _attach_fake_ws(channel: XiaoyiChannel) -> _CapturingWS:
    ws = _CapturingWS()
    channel._ws_connections["ws_url1"] = ws
    return ws


@pytest.mark.asyncio
async def test_message_stream_routes_text_to_bus_and_records_task() -> None:
    bus = MessageBus()
    inbound: list[Any] = []

    async def _capture(msg: Any) -> None:
        inbound.append(msg)

    bus.publish_inbound = _capture  # type: ignore[method-assign]

    channel = XiaoyiChannel(
        XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="a", allow_from=["*"]),
        bus,
    )
    _attach_fake_ws(channel)

    frame = json.dumps(
        {
            "method": "message/stream",
            "id": "m-1",
            "sessionId": "sess-1",
            "params": {
                "id": "task-1",
                "message": {"parts": [{"kind": "text", "text": "hello there"}]},
            },
        }
    )
    await channel._handle_raw_message(frame)

    # Cleanup the session heartbeat that just spun up.
    await channel._stop_session_heartbeat("sess-1")

    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.channel == "xiaoyi"
    assert msg.chat_id == "sess-1"
    assert msg.content == "hello there"
    assert msg.metadata["xiaoyi_session_id"] == "sess-1"
    assert msg.metadata["xiaoyi_task_id"] == "task-1"
    assert channel._session_task_map == {"sess-1": "task-1"}


@pytest.mark.asyncio
async def test_heartbeat_inbound_is_dropped() -> None:
    bus = MessageBus()
    bus.publish_inbound = MagicMock()  # type: ignore[method-assign]
    channel = XiaoyiChannel(XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="a"), bus)

    await channel._handle_raw_message(json.dumps({"msgType": "heartbeat"}))

    bus.publish_inbound.assert_not_called()


@pytest.mark.asyncio
async def test_clear_context_emits_state_cleared_envelope() -> None:
    channel = XiaoyiChannel(
        XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="agent-1"), MessageBus()
    )
    ws = _attach_fake_ws(channel)
    channel._session_task_map["sess-1"] = "task-1"

    await channel._handle_raw_message(
        json.dumps({"method": "clearContext", "id": "rpc-1", "sessionId": "sess-1"})
    )

    assert channel._session_task_map == {}
    assert len(ws.sent) == 1
    envelope = json.loads(ws.sent[0])
    assert envelope["msgType"] == "agent_response"
    assert envelope["agentId"] == "agent-1"
    assert envelope["sessionId"] == "sess-1"
    inner = json.loads(envelope["msgDetail"])
    assert inner["result"]["status"]["state"] == "cleared"


@pytest.mark.asyncio
async def test_tasks_cancel_emits_state_canceled_envelope() -> None:
    channel = XiaoyiChannel(
        XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="agent-1"), MessageBus()
    )
    ws = _attach_fake_ws(channel)

    await channel._handle_raw_message(
        json.dumps(
            {
                "method": "tasks/cancel",
                "id": "rpc-2",
                "sessionId": "sess-1",
                "params": {"id": "task-99"},
            }
        )
    )

    inner = json.loads(json.loads(ws.sent[0])["msgDetail"])
    assert inner["result"]["status"]["state"] == "canceled"
    assert inner["result"]["id"] == "rpc-2"


@pytest.mark.asyncio
async def test_unknown_method_is_ignored() -> None:
    channel = XiaoyiChannel(XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="a"), MessageBus())
    ws = _attach_fake_ws(channel)
    await channel._handle_raw_message(json.dumps({"method": "what/now"}))
    assert ws.sent == []


@pytest.mark.asyncio
async def test_garbage_payload_does_not_raise() -> None:
    channel = XiaoyiChannel(XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="a"), MessageBus())
    _attach_fake_ws(channel)
    await channel._handle_raw_message("not json")


# ---------------------------------------------------------------------------
# Outbound send (A2A artifact-update envelope)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_emits_final_artifact_update() -> None:
    channel = XiaoyiChannel(
        XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="agent-1"), MessageBus()
    )
    ws = _attach_fake_ws(channel)

    outbound = OutboundMessage(
        channel="xiaoyi",
        chat_id="sess-1",
        content="here is your answer",
        metadata={"xiaoyi_session_id": "sess-1", "xiaoyi_task_id": "task-1"},
    )
    await channel.send(outbound)

    assert len(ws.sent) == 1
    envelope = json.loads(ws.sent[0])
    assert envelope["msgType"] == "agent_response"
    assert envelope["agentId"] == "agent-1"
    assert envelope["sessionId"] == "sess-1"
    assert envelope["taskId"] == "task-1"
    inner = json.loads(envelope["msgDetail"])
    result = inner["result"]
    assert result["kind"] == "artifact-update"
    assert result["final"] is True
    assert result["lastChunk"] is True
    assert result["artifact"]["parts"][0]["text"] == "here is your answer"


@pytest.mark.asyncio
async def test_send_extracts_task_id_from_session_map_when_no_metadata() -> None:
    channel = XiaoyiChannel(
        XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="agent-1"), MessageBus()
    )
    ws = _attach_fake_ws(channel)
    channel._session_task_map["sess-2"] = "task-from-map"

    outbound = OutboundMessage(channel="xiaoyi", chat_id="sess-2", content="payload")
    await channel.send(outbound)

    envelope = json.loads(ws.sent[0])
    assert envelope["taskId"] == "task-from-map"


@pytest.mark.asyncio
async def test_send_drops_silently_when_no_connection() -> None:
    channel = XiaoyiChannel(
        XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="agent-1"), MessageBus()
    )
    await channel.send(OutboundMessage(channel="xiaoyi", chat_id="sess-1", content="x"))


@pytest.mark.asyncio
async def test_send_to_two_active_connections_fans_out() -> None:
    channel = XiaoyiChannel(
        XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="agent-1"), MessageBus()
    )
    ws1 = _attach_fake_ws(channel)
    ws2 = _CapturingWS()
    channel._ws_connections["ws_url2"] = ws2

    outbound = OutboundMessage(
        channel="xiaoyi",
        chat_id="sess-1",
        content="duplicated",
        metadata={"xiaoyi_session_id": "sess-1", "xiaoyi_task_id": "task-1"},
    )
    await channel.send(outbound)
    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1


# ---------------------------------------------------------------------------
# Init handshake + app heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_init_message_emits_clawd_bot_init() -> None:
    channel = XiaoyiChannel(
        XiaoyiConfig(enabled=True, ak="k", sk="s", agent_id="agent-42"), MessageBus()
    )
    ws = _attach_fake_ws(channel)
    await channel._send_init_message("ws_url1")
    payload = json.loads(ws.sent[0])
    assert payload == {"msgType": "clawd_bot_init", "agentId": "agent-42"}
