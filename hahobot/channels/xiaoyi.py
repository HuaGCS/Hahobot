"""Huawei Xiaoyi (华为小艺) channel — A2A WebSocket client.

Ported from openJiuwen-ai/jiuwenswarm ``jiuwenclaw/channel/xiaoyi_channel.py``.
Hahobot acts as a client: it opens up to two outbound WebSocket connections
against the Xiaoyi servers (``ws_url1`` + ``ws_url2``), authenticates with
HMAC-SHA256(``sk``, current-millis) carried in the ``x-sign`` header, sends an
initial ``clawd_bot_init`` handshake, then keeps the link alive with a 20-second
application heartbeat. Inbound A2A methods handled: ``message/stream``
(user prompt → bus inbound), ``clearContext``, ``tasks/cancel``. Outbound replies
are wrapped as A2A ``artifact-update`` messages inside an ``agent_response``
envelope. A 5-second per-session heartbeat keeps the Xiaoyi client from timing
out while hahobot is composing a reply.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import ssl
import time
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from hahobot.bus.events import OutboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.channels.base import BaseChannel
from hahobot.config.schema import XiaoyiConfig

_APP_HEARTBEAT_S = 20.0
_SESSION_HEARTBEAT_S = 5.0
_RECONNECT_BACKOFF_S = 5.0
_RECONNECT_CLOSE_DELAY_S = 8.0
_URL_KEYS = ("ws_url1", "ws_url2")


def _generate_signature(sk: str, timestamp: str) -> str:
    """HMAC-SHA256(sk, timestamp) → base64."""
    digest = hmac.new(sk.encode("utf-8"), timestamp.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _generate_auth_headers(ak: str, sk: str, agent_id: str) -> dict[str, str]:
    """Build the four auth headers Xiaoyi expects on the WebSocket upgrade."""
    timestamp = str(int(time.time() * 1000))
    return {
        "x-access-key": ak,
        "x-sign": _generate_signature(sk, timestamp),
        "x-ts": timestamp,
        "x-agent-id": agent_id,
    }


def _ssl_context_for_url(url: str) -> ssl.SSLContext:
    """Disable hostname check when the host is a raw IPv4 literal."""
    ctx = ssl.create_default_context()
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host and host.replace(".", "").isdigit():
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


class XiaoyiChannel(BaseChannel):
    """A2A WebSocket client for Huawei Xiaoyi."""

    name = "xiaoyi"
    display_name = "Xiaoyi (华为小艺)"

    @classmethod
    def default_config(cls) -> dict[str, object]:
        return XiaoyiConfig().model_dump(by_alias=True)

    def __init__(self, config: XiaoyiConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: XiaoyiConfig = config
        self._ws_connections: dict[str, Any] = {}
        self._connect_tasks: dict[str, asyncio.Task[None]] = {}
        self._heartbeat_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_heartbeat_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_task_map: dict[str, str] = {}

    async def start(self) -> None:
        if self._running:
            logger.warning("XiaoyiChannel already running")
            return
        if not self.config.enabled:
            return
        if not self.config.ak or not self.config.sk or not self.config.agent_id:
            logger.error("Xiaoyi channel missing ak/sk/agent_id; not starting")
            return

        self._running = True
        for url_key in _URL_KEYS:
            url = getattr(self.config, url_key, "")
            if url:
                self._connect_tasks[url_key] = asyncio.create_task(
                    self._reconnect_loop(url_key, url)
                )
        if not self._connect_tasks:
            logger.error("Xiaoyi channel has no ws_url1/ws_url2 configured; not starting")
            self._running = False
            return
        logger.info("Xiaoyi channel started (dual-channel client mode)")

    async def stop(self) -> None:
        self._running = False
        for url_key in list(self._heartbeat_tasks):
            task = self._heartbeat_tasks.pop(url_key, None)
            if task and not task.done():
                task.cancel()
        for url_key in list(self._connect_tasks):
            task = self._connect_tasks.pop(url_key, None)
            if task and not task.done():
                task.cancel()
        for session_id in list(self._session_heartbeat_tasks):
            task = self._session_heartbeat_tasks.pop(session_id, None)
            if task and not task.done():
                task.cancel()
        for url_key, ws in list(self._ws_connections.items()):
            if ws is not None:
                try:
                    await ws.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Xiaoyi close failed ({}): {}", url_key, exc)
            self._ws_connections.pop(url_key, None)
        logger.info("Xiaoyi channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """Forward an outbound bot reply as an A2A ``artifact-update`` (final)."""
        if not self._ws_connections:
            logger.warning("Xiaoyi send dropped: no active connection")
            return
        session_id, task_id = self._extract_platform_receive_info(msg)
        text = self._extract_outbound_text(msg)
        for url_key, ws in list(self._ws_connections.items()):
            if ws is None:
                continue
            try:
                await self._send_text_response(
                    session_id, task_id, text, url_key, is_final=True
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Xiaoyi send failed ({}): {}", url_key, exc)
        if session_id:
            await self._stop_session_heartbeat(session_id)

    # ------------------------------------------------------------------
    # Connect / reconnect loop
    # ------------------------------------------------------------------

    async def _reconnect_loop(self, url_key: str, url: str) -> None:
        while self._running:
            try:
                await self._connect(url_key, url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Xiaoyi connect failed ({}): {}", url, exc)
                await asyncio.sleep(_RECONNECT_BACKOFF_S)

    async def _connect(self, url_key: str, url: str) -> None:
        import websockets

        headers = _generate_auth_headers(self.config.ak, self.config.sk, self.config.agent_id)
        ssl_ctx = _ssl_context_for_url(url)

        async with websockets.connect(url, additional_headers=headers, ssl=ssl_ctx) as ws:
            self._ws_connections[url_key] = ws
            logger.info("Xiaoyi connected ({}): {}", url_key, url)
            await self._send_init_message(url_key)
            self._heartbeat_tasks[url_key] = asyncio.create_task(
                self._heartbeat_loop(url_key)
            )
            try:
                async for raw in ws:
                    await self._handle_raw_message(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Xiaoyi connection lost ({}): {}", url_key, exc)
            finally:
                hb = self._heartbeat_tasks.pop(url_key, None)
                if hb and not hb.done():
                    hb.cancel()
                self._ws_connections.pop(url_key, None)
                logger.info("Xiaoyi connection closed ({}): {}", url_key, url)
                await asyncio.sleep(_RECONNECT_CLOSE_DELAY_S)

    async def _send_init_message(self, url_key: str) -> None:
        ws = self._ws_connections.get(url_key)
        if ws is None:
            return
        payload = {"msgType": "clawd_bot_init", "agentId": self.config.agent_id}
        await ws.send(json.dumps(payload))
        logger.info("Xiaoyi sent clawd_bot_init ({})", url_key)

    async def _heartbeat_loop(self, url_key: str) -> None:
        while self._running and self._ws_connections.get(url_key) is not None:
            ws = self._ws_connections.get(url_key)
            if ws is None:
                break
            try:
                await ws.send(
                    json.dumps({"msgType": "heartbeat", "agentId": self.config.agent_id})
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Xiaoyi heartbeat failed ({}): {}", url_key, exc)
                break
            await asyncio.sleep(_APP_HEARTBEAT_S)

    # ------------------------------------------------------------------
    # Inbound dispatch (testable without a real websocket)
    # ------------------------------------------------------------------

    async def _handle_raw_message(self, raw: str | bytes) -> None:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Xiaoyi: JSON decode failure on inbound frame")
            return
        msg_type = message.get("msgType")
        if msg_type == "heartbeat":
            return
        method = message.get("method")
        if method == "message/stream":
            await self._handle_message_stream(message)
        elif method == "clearContext":
            await self._handle_clear_context(message)
        elif method == "tasks/cancel":
            await self._handle_tasks_cancel(message)
        else:
            logger.warning("Xiaoyi: unknown inbound method {!r}", method)

    async def _handle_message_stream(self, message: dict[str, Any]) -> None:
        params = message.get("params") or {}
        session_id = (
            message.get("sessionId") or params.get("sessionId") or ""
        )
        task_id = params.get("id") or ""
        user_message = params.get("message") or {}
        parts = user_message.get("parts") or []
        text = ""
        for part in parts:
            if part.get("kind") == "text":
                text = part.get("text") or ""
                break

        if session_id:
            self._session_task_map[session_id] = task_id

        await self._handle_message(
            sender_id="xiaoyi",
            chat_id=session_id or task_id or message.get("id", ""),
            content=text,
            metadata={
                "method": "message/stream",
                "xiaoyi_session_id": session_id,
                "xiaoyi_task_id": task_id,
            },
        )

        if session_id:
            await self._start_session_heartbeat(session_id, task_id)

    async def _handle_clear_context(self, message: dict[str, Any]) -> None:
        session_id = message.get("sessionId") or ""
        logger.info("Xiaoyi clearContext: session={}", session_id)
        if session_id:
            self._session_task_map.pop(session_id, None)
        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"status": {"state": "cleared"}},
        }
        for url_key in list(self._ws_connections):
            await self._send_agent_response(session_id, session_id, response, url_key)

    async def _handle_tasks_cancel(self, message: dict[str, Any]) -> None:
        session_id = message.get("sessionId") or ""
        params = message.get("params") or {}
        task_id = params.get("id") or message.get("taskId") or ""
        logger.info("Xiaoyi tasks/cancel: session={} task={}", session_id, task_id)
        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"id": message.get("id", ""), "status": {"state": "canceled"}},
        }
        for url_key in list(self._ws_connections):
            await self._send_agent_response(session_id, task_id, response, url_key)

    # ------------------------------------------------------------------
    # Session-level keep-alive
    # ------------------------------------------------------------------

    async def _start_session_heartbeat(self, session_id: str, task_id: str) -> None:
        await self._stop_session_heartbeat(session_id)

        async def _heartbeat_loop() -> None:
            try:
                while self._running:
                    await asyncio.sleep(_SESSION_HEARTBEAT_S)
                    for url_key, ws in list(self._ws_connections.items()):
                        if ws is None:
                            continue
                        try:
                            await self._send_text_response(
                                session_id, task_id, "", url_key, is_final=False
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "Xiaoyi session heartbeat send failed ({}): {}", url_key, exc
                            )
            except asyncio.CancelledError:
                logger.debug("Xiaoyi session heartbeat cancelled: {}", session_id)

        self._session_heartbeat_tasks[session_id] = asyncio.create_task(_heartbeat_loop())

    async def _stop_session_heartbeat(self, session_id: str) -> None:
        task = self._session_heartbeat_tasks.pop(session_id, None)
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # A2A reply helpers
    # ------------------------------------------------------------------

    def _extract_platform_receive_info(self, msg: OutboundMessage) -> tuple[str, str]:
        """Pull the platform session_id / task_id, preferring metadata."""
        meta = msg.metadata or {}
        platform_session_id = str(meta.get("xiaoyi_session_id") or "").strip()
        platform_task_id = str(meta.get("xiaoyi_task_id") or "").strip()
        if platform_session_id or platform_task_id:
            return (
                platform_session_id or msg.chat_id or "",
                platform_task_id or platform_session_id,
            )
        session_id = msg.chat_id or ""
        task_id = self._session_task_map.get(session_id, session_id)
        return session_id, task_id

    @staticmethod
    def _extract_outbound_text(msg: OutboundMessage) -> str:
        content = msg.content
        if isinstance(content, dict):
            return str(content.get("output", content))
        return str(content or "")

    async def _send_text_response(
        self,
        session_id: str,
        task_id: str,
        text: str,
        url_key: str,
        *,
        is_final: bool,
    ) -> None:
        now_ms = int(time.time() * 1000)
        response = {
            "jsonrpc": "2.0",
            "id": f"msg_{now_ms}",
            "result": {
                "taskId": task_id,
                "kind": "artifact-update",
                "append": False,
                "lastChunk": is_final,
                "final": is_final,
                "artifact": {
                    "artifactId": f"artifact_{now_ms}",
                    "parts": [{"kind": "text", "text": text}],
                },
            },
        }
        await self._send_agent_response(session_id, task_id, response, url_key)

    async def _send_agent_response(
        self,
        session_id: str,
        task_id: str,
        response: dict[str, Any],
        url_key: str,
    ) -> None:
        ws = self._ws_connections.get(url_key)
        if ws is None:
            return
        envelope = {
            "msgType": "agent_response",
            "agentId": self.config.agent_id,
            "sessionId": session_id,
            "taskId": task_id,
            "msgDetail": json.dumps(response),
        }
        await ws.send(json.dumps(envelope))
