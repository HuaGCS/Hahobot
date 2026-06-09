"""Standard A2A (Agent2Agent) JSON-RPC server adapter for aiohttp.

This module registers routes on an existing ``aiohttp.web.Application`` so that
the hahobot runtime speaks the A2A protocol (v0.3.0):

* ``GET /.well-known/agent-card.json`` — Agent Card discovery endpoint.
* ``GET /.well-known/agent.json`` — Legacy alias.
* ``POST /a2a`` — JSON-RPC endpoint for A2A methods.

Supported A2A methods:

* ``message/send`` — Send a message and get a completed (non-streaming) task.
* ``tasks/get`` — Retrieve a stored task by id.
* ``tasks/cancel`` — Cancel a non-terminal task.

* ``message/stream`` — Stream a message response via SSE.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from aiohttp import web
from loguru import logger

from hahobot.a2a.models import (
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    STATE_CANCELED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_WORKING,
    TASK_NOT_CANCELABLE,
    TASK_NOT_FOUND,
    UNSUPPORTED_OPERATION,
    build_agent_card,
    extract_text_from_parts,
    make_artifact_update_event,
    make_initial_task,
    make_message,
    make_status_update_event,
    make_task,
)
from hahobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

# ---------------------------------------------------------------------------
# App keys
# ---------------------------------------------------------------------------

A2A_AGENT_LOOP_KEY = web.AppKey("a2a_agent_loop", Any)
A2A_CARD_KEY = web.AppKey("a2a_card", dict[str, Any])
A2A_TASKS_KEY = web.AppKey("a2a_tasks", dict[str, dict[str, Any]])
A2A_TIMEOUT_KEY = web.AppKey("a2a_timeout", float)
A2A_MAX_TASKS_KEY = web.AppKey("a2a_max_tasks", int)
A2A_STREAMING_KEY = web.AppKey("a2a_streaming", bool)

# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _response_text(value: Any) -> str:
    """Normalize ``process_direct`` output to plain assistant text.

    Mirrors the same logic used in ``hahobot/api/server.py``.
    """
    if value is None:
        return ""
    if hasattr(value, "content"):
        return str(value.content or "")
    return str(value)


def _jsonrpc_error(
    req_id: Any, code: int, message: str, http_status: int = 200
) -> web.Response:
    """A2A-compatible JSON-RPC error response (defaults to HTTP 200)."""
    return web.json_response(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        status=http_status,
    )


def _jsonrpc_result(req_id: Any, result: dict[str, Any]) -> web.Response:
    return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": result})


async def _sse_write(
    resp: web.StreamResponse, req_id: Any, event: dict[str, Any]
) -> None:
    """Write a single SSE data frame."""
    payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": event})
    await resp.write(b"data: " + payload.encode("utf-8") + b"\n\n")


# ---------------------------------------------------------------------------
# Bounded task store helpers
# ---------------------------------------------------------------------------


def _store_task(app: web.Application, task: dict[str, Any]) -> None:
    """Insert *task* into the bounded in-memory store, evicting oldest entries."""
    store = app[A2A_TASKS_KEY]
    task_id = task["id"]
    store[task_id] = task
    max_tasks = app[A2A_MAX_TASKS_KEY]
    while len(store) > max_tasks:
        # Dicts are insertion-ordered (Python 3.7+); pop the first key.
        try:
            oldest = next(iter(store))
            del store[oldest]
        except StopIteration:
            break


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def handle_agent_card(request: web.Request) -> web.Response:
    """GET /.well-known/agent-card.json — return the Agent Card."""
    return web.json_response(request.app[A2A_CARD_KEY])


async def handle_a2a_rpc(request: web.Request) -> web.Response:
    """POST /a2a — JSON-RPC dispatch."""
    # --- Parse body ---
    try:
        body = await request.json()
    except Exception:
        return _jsonrpc_error(None, JSONRPC_PARSE_ERROR, "Parse error")

    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        return _jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Invalid request")
    method = body.get("method")
    if not isinstance(method, str) or not method.strip():
        return _jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Missing or invalid method")

    req_id = body.get("id")
    params = body.get("params") or {}

    # --- Dispatch ---
    if method == "message/send":
        return await _handle_message_send(request, req_id, params)
    if method == "message/stream":
        if not request.app.get(A2A_STREAMING_KEY, False):
            return _jsonrpc_error(req_id, UNSUPPORTED_OPERATION, "Streaming is not supported")
        return await _handle_message_stream(request, req_id, params)
    if method == "tasks/get":
        return await _handle_tasks_get(request, req_id, params)
    if method == "tasks/cancel":
        return await _handle_tasks_cancel(request, req_id, params)

    return _jsonrpc_error(req_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")


async def _handle_message_send(
    request: web.Request, req_id: Any, params: dict[str, Any]
) -> web.Response:
    """Handle ``message/send`` — process the user message synchronously."""
    message = params.get("message")
    if not isinstance(message, dict):
        return _jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "Missing or invalid 'message'")
    parts = message.get("parts")
    if not isinstance(parts, list):
        return _jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "Missing or invalid 'message.parts'")

    # Extract text from the incoming parts.
    try:
        text = extract_text_from_parts(parts)
    except ValueError as exc:
        return _jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, str(exc))

    context_id = message.get("contextId") or uuid.uuid4().hex
    task_id = uuid.uuid4().hex
    user_msg_id = message.get("messageId") or uuid.uuid4().hex

    # Build the user echo message for history.
    user_echo = make_message(
        role="user", text=text, message_id=user_msg_id, context_id=context_id, task_id=task_id
    )
    history = [user_echo]

    # Call the agent runtime.
    agent_loop = request.app[A2A_AGENT_LOOP_KEY]
    timeout = request.app[A2A_TIMEOUT_KEY]

    try:
        response = await asyncio.wait_for(
            agent_loop.process_direct(
                content=text,
                session_key=f"a2a:{context_id}",
                channel="a2a",
                chat_id=context_id,
            ),
            timeout=timeout,
        )
        agent_text = _response_text(response)
        if not agent_text or not agent_text.strip():
            agent_text = EMPTY_FINAL_RESPONSE_MESSAGE
        state = STATE_COMPLETED
    except TimeoutError:
        logger.warning("A2A request timed out for context_id={}", context_id)
        agent_text = "Request timed out."
        state = STATE_FAILED
    except Exception:
        logger.exception("A2A request failed for context_id={}", context_id)
        agent_text = "Internal error processing request."
        state = STATE_FAILED

    # Append the agent response to history.
    agent_msg = make_message(
        role="agent",
        text=agent_text,
        message_id=uuid.uuid4().hex,
        context_id=context_id,
        task_id=task_id,
    )
    history.append(agent_msg)

    task = make_task(
        task_id=task_id,
        context_id=context_id,
        state=state,
        agent_text=agent_text,
        history=history,
    )

    _store_task(request.app, task)
    return _jsonrpc_result(req_id, task)


async def _handle_tasks_get(
    request: web.Request, req_id: Any, params: dict[str, Any]
) -> web.Response:
    """Handle ``tasks/get`` — look up a stored task by id."""
    task_id = params.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        return _jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "Missing or invalid 'id'")

    store = request.app[A2A_TASKS_KEY]
    task = store.get(task_id)
    if task is None:
        return _jsonrpc_error(req_id, TASK_NOT_FOUND, f"Task not found: {task_id}")

    # Optional historyLength — slice history without mutating the stored task.
    history_length = params.get("historyLength")
    if isinstance(history_length, int) and history_length >= 0 and task.get("history"):
        result = dict(task)  # shallow copy
        result["history"] = task["history"][-history_length:]
        return _jsonrpc_result(req_id, result)

    return _jsonrpc_result(req_id, task)


async def _handle_tasks_cancel(
    request: web.Request, req_id: Any, params: dict[str, Any]
) -> web.Response:
    """Handle ``tasks/cancel`` — mark a non-terminal task as canceled."""
    task_id = params.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        return _jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "Missing or invalid 'id'")

    store = request.app[A2A_TASKS_KEY]
    task = store.get(task_id)
    if task is None:
        return _jsonrpc_error(req_id, TASK_NOT_FOUND, f"Task not found: {task_id}")

    current_state = task.get("status", {}).get("state", "")
    if current_state in (STATE_COMPLETED, STATE_FAILED, STATE_CANCELED):
        return _jsonrpc_error(req_id, TASK_NOT_CANCELABLE, "Task is already in a terminal state")

    # Mutate the stored task in place.
    task["status"]["state"] = STATE_CANCELED
    task["status"]["timestamp"] = datetime.now(UTC).isoformat()
    return _jsonrpc_result(req_id, task)


async def _handle_message_stream(
    request: web.Request, req_id: Any, params: dict[str, Any]
) -> web.StreamResponse:
    """Handle ``message/stream`` — stream the agent response via SSE.

    Validates the request before starting the SSE stream; on validation
    failure a standard JSON-RPC error response is returned instead.
    Once the SSE response is prepared, all events (initial Task,
    artifact-update deltas, final status-update) are written as SSE frames.
    The final task is also stored so that ``tasks/get`` can retrieve it.
    """
    message = params.get("message")
    if not isinstance(message, dict):
        return _jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "Missing or invalid 'message'")
    parts = message.get("parts")
    if not isinstance(parts, list):
        return _jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "Missing or invalid 'message.parts'")

    try:
        text = extract_text_from_parts(parts)
    except ValueError as exc:
        return _jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, str(exc))

    context_id = message.get("contextId") or uuid.uuid4().hex
    task_id = uuid.uuid4().hex
    user_msg_id = message.get("messageId") or uuid.uuid4().hex

    user_echo = make_message(
        role="user", text=text, message_id=user_msg_id, context_id=context_id, task_id=task_id
    )
    history = [user_echo]

    # Prepare SSE stream response.
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)

    artifact_id = uuid.uuid4().hex
    chunks_sent: int = 0
    collected: list[str] = []
    agent_loop = request.app[A2A_AGENT_LOOP_KEY]
    timeout = request.app[A2A_TIMEOUT_KEY]

    # --- Send initial Task event (state=working, no artifacts) ---
    try:
        initial_task = make_initial_task(
            task_id=task_id, context_id=context_id, state=STATE_WORKING, history=history
        )
        await _sse_write(resp, req_id, initial_task)
    except Exception:
        return resp

    # --- Set up streaming callbacks ---
    async def on_stream(delta: str) -> None:
        nonlocal chunks_sent
        try:
            event = make_artifact_update_event(
                task_id=task_id,
                context_id=context_id,
                artifact_id=artifact_id,
                text=delta,
                append=(chunks_sent > 0),
                last_chunk=False,
            )
            await _sse_write(resp, req_id, event)
            chunks_sent += 1
            collected.append(delta)
        except Exception:
            pass

    async def on_stream_end(*, resuming: bool) -> None:
        pass

    # --- Run the agent ---
    full_text = ""
    state = STATE_COMPLETED

    try:
        response = await asyncio.wait_for(
            agent_loop.process_direct(
                content=text,
                session_key=f"a2a:{context_id}",
                channel="a2a",
                chat_id=context_id,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
            ),
            timeout=timeout,
        )

        final_text = _response_text(response)
        if not final_text or not final_text.strip():
            final_text = EMPTY_FINAL_RESPONSE_MESSAGE

        if chunks_sent == 0:
            # No streamed chunks — send one artifact update with the full text.
            try:
                event = make_artifact_update_event(
                    task_id=task_id,
                    context_id=context_id,
                    artifact_id=artifact_id,
                    text=final_text,
                    append=False,
                    last_chunk=True,
                )
                await _sse_write(resp, req_id, event)
            except Exception:
                pass
            full_text = final_text
        else:
            full_text = "".join(collected)
    except TimeoutError:
        logger.warning("A2A stream timed out for context_id={}", context_id)
        state = STATE_FAILED
        full_text = "Request timed out."
    except Exception:
        logger.exception("A2A stream failed for context_id={}", context_id)
        state = STATE_FAILED
        full_text = "Internal error processing request."

    # --- Build and store the final task ---
    agent_msg = make_message(
        role="agent",
        text=full_text,
        message_id=uuid.uuid4().hex,
        context_id=context_id,
        task_id=task_id,
    )
    history.append(agent_msg)

    stored_task = make_task(
        task_id=task_id,
        context_id=context_id,
        state=state,
        agent_text=full_text,
        history=history,
    )
    _store_task(request.app, stored_task)

    # --- Send final status-update ---
    try:
        status_event = make_status_update_event(
            task_id=task_id, context_id=context_id, state=state, final=True
        )
        await _sse_write(resp, req_id, status_event)
    except Exception:
        pass

    try:
        await resp.write_eof()
    except Exception:
        pass
    return resp


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_a2a_routes(
    app: web.Application,
    agent_loop: Any,
    *,
    name: str,
    description: str,
    base_url: str,
    version: str,
    timeout: float = 120.0,
    max_tasks: int = 2048,
    streaming: bool = False,
) -> None:
    """Register A2A routes on an existing ``aiohttp.web.Application``.

    Args:
        app: The aiohttp app to attach routes to.
        agent_loop: An object with a ``process_direct`` method.
        name: Human-readable agent name for the Agent Card.
        description: Short description for the Agent Card.
        base_url: Public-facing base URL (e.g. ``http://192.168.1.10:8900``).
        version: Agent semantic version.
        timeout: Per-request timeout in seconds.
        max_tasks: Maximum number of stored tasks before oldest eviction.
        streaming: Whether streaming is advertised in the Agent Card.
    """
    rpc_url = base_url.rstrip("/") + "/a2a"

    card = build_agent_card(
        name=name,
        description=description,
        url=rpc_url,
        version=version,
        streaming=streaming,
    )

    app[A2A_AGENT_LOOP_KEY] = agent_loop
    app[A2A_CARD_KEY] = card
    app[A2A_TASKS_KEY] = {}
    app[A2A_TIMEOUT_KEY] = timeout
    app[A2A_MAX_TASKS_KEY] = max_tasks
    app[A2A_STREAMING_KEY] = streaming

    app.router.add_get("/.well-known/agent-card.json", handle_agent_card)
    app.router.add_get("/.well-known/agent.json", handle_agent_card)
    app.router.add_post("/a2a", handle_a2a_rpc)
