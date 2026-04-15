"""OpenAI-compatible HTTP API server for a fixed hahobot session.

Provides /v1/chat/completions and /v1/models endpoints.
All requests route to a single persistent API session.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from aiohttp import web
from loguru import logger

from hahobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

API_SESSION_KEY = "api:default"
API_CHAT_ID = "default"
AGENT_LOOP_KEY = web.AppKey("agent_loop", Any)
MODEL_NAME_KEY = web.AppKey("model_name", str)
REQUEST_TIMEOUT_KEY = web.AppKey("request_timeout", float)
SESSION_LOCKS_KEY = web.AppKey("session_locks", dict[str, asyncio.Lock])
MAX_SESSION_LOCKS_KEY = web.AppKey("max_session_locks", int)
_MISSING = object()


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _error_json(status: int, message: str, err_type: str = "invalid_request_error") -> web.Response:
    return web.json_response(
        {"error": {"message": message, "type": err_type, "code": status}},
        status=status,
    )


def _chat_completion_response(content: str, model: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _response_text(value: Any) -> str:
    """Normalize process_direct output to plain assistant text."""
    if value is None:
        return ""
    if hasattr(value, "content"):
        return str(getattr(value, "content") or "")
    return str(value)


def _app_value(app, key, legacy_key: str, default: Any = _MISSING) -> Any:
    """Read aiohttp AppKey state while preserving legacy string-key compatibility."""
    if key in app:
        return app[key]
    if legacy_key in app:
        return app[legacy_key]
    if default is not _MISSING:
        return default
    raise KeyError(key)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def handle_chat_completions(request: web.Request) -> web.Response:
    """POST /v1/chat/completions"""

    # --- Parse body ---
    try:
        body = await request.json()
    except Exception:
        return _error_json(400, "Invalid JSON body")

    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) != 1:
        return _error_json(400, "Only a single user message is supported")

    # Stream not yet supported
    if body.get("stream", False):
        return _error_json(400, "stream=true is not supported yet. Set stream=false or omit it.")

    message = messages[0]
    if not isinstance(message, dict) or message.get("role") != "user":
        return _error_json(400, "Only a single user message is supported")
    user_content = message.get("content", "")
    if isinstance(user_content, list):
        # Multi-modal content array — extract text parts
        user_content = " ".join(
            part.get("text", "") for part in user_content if part.get("type") == "text"
        )

    agent_loop = _app_value(request.app, AGENT_LOOP_KEY, "agent_loop")
    timeout_s = _app_value(request.app, REQUEST_TIMEOUT_KEY, "request_timeout", 120.0)
    model_name = _app_value(request.app, MODEL_NAME_KEY, "model_name", "hahobot")
    if (requested_model := body.get("model")) and requested_model != model_name:
        return _error_json(400, f"Only configured model '{model_name}' is available")

    session_key = f"api:{body['session_id']}" if body.get("session_id") else API_SESSION_KEY
    session_locks = _app_value(request.app, SESSION_LOCKS_KEY, "session_locks", {})
    session_lock = session_locks.setdefault(session_key, asyncio.Lock())

    # Prune unlocked entries when the cache grows too large.
    max_locks = _app_value(request.app, MAX_SESSION_LOCKS_KEY, "_max_session_locks", 1024)
    if len(session_locks) > max_locks:
        to_remove = [k for k, v in session_locks.items() if not v.locked()]
        for k in to_remove[: len(session_locks) - max_locks]:
            session_locks.pop(k, None)

    logger.info("API request session_key={} content={}", session_key, user_content[:80])

    fallback = EMPTY_FINAL_RESPONSE_MESSAGE

    try:
        async with session_lock:
            try:
                response = await asyncio.wait_for(
                    agent_loop.process_direct(
                        content=user_content,
                        session_key=session_key,
                        channel="api",
                        chat_id=API_CHAT_ID,
                    ),
                    timeout=timeout_s,
                )
                response_text = _response_text(response)

                if not response_text or not response_text.strip():
                    logger.warning(
                        "Empty response for session {}, retrying",
                        session_key,
                    )
                    retry_response = await asyncio.wait_for(
                        agent_loop.process_direct(
                            content=user_content,
                            session_key=session_key,
                            channel="api",
                            chat_id=API_CHAT_ID,
                        ),
                        timeout=timeout_s,
                    )
                    response_text = _response_text(retry_response)
                    if not response_text or not response_text.strip():
                        logger.warning(
                            "Empty response after retry for session {}, using fallback",
                            session_key,
                        )
                        response_text = fallback

            except asyncio.TimeoutError:
                return _error_json(504, f"Request timed out after {timeout_s}s")
            except Exception:
                logger.exception("Error processing request for session {}", session_key)
                return _error_json(500, "Internal server error", err_type="server_error")
    except Exception:
        logger.exception("Unexpected API lock error for session {}", session_key)
        return _error_json(500, "Internal server error", err_type="server_error")

    return web.json_response(_chat_completion_response(response_text, model_name))


async def handle_models(request: web.Request) -> web.Response:
    """GET /v1/models"""
    model_name = _app_value(request.app, MODEL_NAME_KEY, "model_name", "hahobot")
    return web.json_response({
        "object": "list",
        "data": [
            {
                "id": model_name,
                "object": "model",
                "created": 0,
                "owned_by": "hahobot",
            }
        ],
    })


async def handle_health(request: web.Request) -> web.Response:
    """GET /health"""
    return web.json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(agent_loop, model_name: str = "hahobot", request_timeout: float = 120.0) -> web.Application:
    """Create the aiohttp application.

    Args:
        agent_loop: An initialized AgentLoop instance.
        model_name: Model name reported in responses.
        request_timeout: Per-request timeout in seconds.
    """
    app = web.Application()
    app[AGENT_LOOP_KEY] = agent_loop
    app[MODEL_NAME_KEY] = model_name
    app[REQUEST_TIMEOUT_KEY] = request_timeout
    # Bounded session lock cache to prevent unbounded memory growth.
    # Uses a simple LRU-style dict: when the cache exceeds the limit, the
    # oldest entries (those not currently held) are pruned.
    max_session_locks = 1024
    app[SESSION_LOCKS_KEY] = {}  # per-user locks, keyed by session_key
    app[MAX_SESSION_LOCKS_KEY] = max_session_locks

    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_get("/health", handle_health)
    return app
