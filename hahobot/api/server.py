"""OpenAI-compatible HTTP API server for a fixed hahobot session.

Provides /v1/chat/completions and /v1/models endpoints.
All requests route to a single persistent API session.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import mimetypes
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from aiohttp import web
from aiohttp.web_request import FileField
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
_API_CLIENT_MAX_SIZE = 16 * 1024**2
_MAX_INLINE_FILE_BYTES = 2 * 1024**2
_MAX_INLINE_FILE_CHARS = 24_000
_MAX_USER_CONTENT_CHARS = 96_000
_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[^;,]+)?(?:;charset=[^;,]+)?;base64,(?P<data>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_TEXTUAL_MIME_TYPES = {
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
    "application/x-json",
    "application/x-ndjson",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
    "application/x-toml",
}
_TEXTUAL_EXTENSIONS = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".kt",
    ".log",
    ".lua",
    ".md",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass(slots=True)
class _ApiAttachment:
    filename: str
    mime_type: str
    raw: bytes
    source: str


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


def _parse_boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _guess_mime_type(filename: str, hinted_mime: str | None = None) -> str:
    if hinted_mime:
        return hinted_mime
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _decode_base64_payload(data: str) -> tuple[str | None, bytes]:
    payload = data.strip()
    hinted_mime: str | None = None

    match = _DATA_URL_RE.match(payload)
    if match is not None:
        hinted_mime = match.group("mime") or None
        payload = match.group("data")
    elif "://" in payload:
        raise ValueError("Remote file URLs are not supported; send base64 or a data URL instead.")

    payload = re.sub(r"\s+", "", payload)
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 file data.") from exc
    return hinted_mime, raw


def _decode_text_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _looks_textual_attachment(filename: str, mime_type: str, raw: bytes) -> bool:
    mime = mime_type.lower()
    if mime.startswith("text/") or mime in _TEXTUAL_MIME_TYPES:
        return True
    if any(filename.lower().endswith(ext) for ext in _TEXTUAL_EXTENSIONS):
        return True
    if b"\x00" in raw:
        return False
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def _render_attachment(attachment: _ApiAttachment) -> str:
    label = (
        f"[Attached file: {attachment.filename} "
        f"({attachment.mime_type}, {len(attachment.raw)} bytes; {attachment.source})]"
    )
    if len(attachment.raw) > _MAX_INLINE_FILE_BYTES:
        return (
            f"{label}\n"
            f"[File omitted: exceeds {_MAX_INLINE_FILE_BYTES} bytes for direct API extraction.]"
        )

    mime = attachment.mime_type.lower()
    if mime.startswith("image/"):
        return f"{label}\n[Image omitted from direct API path.]"

    if not _looks_textual_attachment(attachment.filename, attachment.mime_type, attachment.raw):
        return f"{label}\n[Binary file omitted from direct API path.]"

    text = _decode_text_bytes(attachment.raw)
    if attachment.filename.lower().endswith(".json") or mime in {
        "application/json",
        "application/ld+json",
        "application/x-json",
    }:
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except Exception:
            pass
    return f"{label}\n{_truncate_text(text, _MAX_INLINE_FILE_CHARS)}"


def _image_placeholder(block: dict[str, Any], index: int) -> str:
    meta = block.get("_meta") if isinstance(block.get("_meta"), dict) else {}
    path = meta.get("path")
    if isinstance(path, str) and path.strip():
        return f"[Attached image omitted from direct API path: {path}]"
    return f"[Attached image omitted from direct API path: image-{index}]"


def _attachment_from_inline_block(block: dict[str, Any], index: int) -> _ApiAttachment:
    payload = block.get("file")
    if not isinstance(payload, dict):
        payload = block

    filename = payload.get("filename") or payload.get("name") or block.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        filename = f"inline-file-{index}"

    hinted_mime = (
        payload.get("mime_type")
        or payload.get("mimeType")
        or block.get("mime_type")
        or block.get("mimeType")
    )
    if hinted_mime is not None and not isinstance(hinted_mime, str):
        raise ValueError(f"Inline file block {index} has an invalid MIME type.")

    encoded = None
    for key in ("file_data", "fileData", "data", "file_url", "fileUrl", "url"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            encoded = candidate
            break
        candidate = block.get(key)
        if isinstance(candidate, str) and candidate.strip():
            encoded = candidate
            break

    if encoded is None:
        raise ValueError(
            f"Inline file block {index} is missing base64 file data "
            "(expected file_data / data / data URL)."
        )

    data_url_mime, raw = _decode_base64_payload(encoded)
    mime_type = _guess_mime_type(filename, hinted_mime or data_url_mime)
    return _ApiAttachment(
        filename=filename,
        mime_type=mime_type,
        raw=raw,
        source=f"inline content block #{index}",
    )


def _compose_user_content(content: Any, attachments: list[_ApiAttachment]) -> str:
    sections: list[str] = []

    if isinstance(content, str):
        if content.strip():
            sections.append(content)
    elif isinstance(content, list):
        for index, block in enumerate(content, start=1):
            if not isinstance(block, dict):
                raise ValueError(f"Content block {index} must be an object.")

            block_type = block.get("type")
            if block_type in {"text", "input_text"}:
                text = block.get("text")
                if not isinstance(text, str):
                    raise ValueError(f"Text block {index} must contain a string 'text' value.")
                if text.strip():
                    sections.append(text)
                continue
            if block_type in {"image_url", "input_image"}:
                sections.append(_image_placeholder(block, index))
                continue
            if block_type in {"file", "input_file"}:
                sections.append(_render_attachment(_attachment_from_inline_block(block, index)))
                continue
            raise ValueError(f"Unsupported content block type: {block_type!r}.")
    else:
        raise ValueError("User message content must be a string or a content array.")

    for attachment in attachments:
        sections.append(_render_attachment(attachment))

    combined = "\n\n".join(section for section in sections if section and section.strip())
    if not combined.strip():
        raise ValueError("User message content is empty after processing.")
    return _truncate_text(combined, _MAX_USER_CONTENT_CHARS)


async def _parse_request_body(request: web.Request) -> tuple[dict[str, Any], list[_ApiAttachment]]:
    content_type = getattr(request, "content_type", "")
    if isinstance(content_type, str) and content_type.startswith("multipart/"):
        try:
            form = await request.post()
        except Exception as exc:
            raise ValueError("Invalid multipart body") from exc

        body: dict[str, Any] = {}
        attachments: list[_ApiAttachment] = []

        for key in form.keys():
            for value in form.getall(key):
                if isinstance(value, FileField):
                    filename = value.filename or key
                    attachments.append(
                        _ApiAttachment(
                            filename=filename,
                            mime_type=_guess_mime_type(filename, value.content_type),
                            raw=value.file.read(),
                            source=f"multipart field '{key}'",
                        )
                    )
                    continue
                body[key] = value

        raw_messages = body.get("messages")
        if isinstance(raw_messages, str):
            try:
                body["messages"] = json.loads(raw_messages)
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid JSON in multipart field 'messages'.") from exc
        if "stream" in body:
            body["stream"] = _parse_boolish(body["stream"])
        return body, attachments

    try:
        body = await request.json()
    except Exception as exc:
        raise ValueError("Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object.")
    return body, []


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def handle_chat_completions(request: web.Request) -> web.Response:
    """POST /v1/chat/completions"""

    # --- Parse body ---
    try:
        body, uploaded_files = await _parse_request_body(request)
    except ValueError as exc:
        return _error_json(400, str(exc))

    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) != 1:
        return _error_json(400, "Only a single user message is supported")

    # Stream not yet supported
    if body.get("stream", False):
        return _error_json(400, "stream=true is not supported yet. Set stream=false or omit it.")

    message = messages[0]
    if not isinstance(message, dict) or message.get("role") != "user":
        return _error_json(400, "Only a single user message is supported")

    try:
        user_content = _compose_user_content(message.get("content", ""), uploaded_files)
    except ValueError as exc:
        return _error_json(400, str(exc))

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
    app = web.Application(client_max_size=_API_CLIENT_MAX_SIZE)
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
