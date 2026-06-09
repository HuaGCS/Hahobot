"""Pure data helpers for A2A JSON-RPC message construction.

No aiohttp or runtime imports — can be used by any module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# JSON-RPC 2.0 error codes
# ---------------------------------------------------------------------------

JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600
JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_INVALID_PARAMS: int = -32602
JSONRPC_INTERNAL_ERROR: int = -32603

# ---------------------------------------------------------------------------
# A2A-specific error codes
# ---------------------------------------------------------------------------

TASK_NOT_FOUND: int = -32001
TASK_NOT_CANCELABLE: int = -32002
UNSUPPORTED_OPERATION: int = -32004
CONTENT_TYPE_NOT_SUPPORTED: int = -32005

# ---------------------------------------------------------------------------
# Task state strings
# ---------------------------------------------------------------------------

STATE_SUBMITTED: str = "submitted"
STATE_WORKING: str = "working"
STATE_COMPLETED: str = "completed"
STATE_FAILED: str = "failed"
STATE_CANCELED: str = "canceled"


# ---------------------------------------------------------------------------
# Agent Card builder
# ---------------------------------------------------------------------------


def build_agent_card(
    *,
    name: str,
    description: str,
    url: str,
    version: str,
    default_input_modes: list[str] | None = None,
    default_output_modes: list[str] | None = None,
    streaming: bool = False,
) -> dict[str, Any]:
    """Build a standard A2A Agent Card dictionary.

    Args:
        name: Human-readable agent name.
        description: Brief description of the agent.
        url: JSON-RPC endpoint URL (e.g. ``http://host:port/a2a``).
        version: Semantic version of the agent.
        default_input_modes: Accepted content types (default ``["text/plain"]``).
        default_output_modes: Produced content types (default ``["text/plain"]``).
        streaming: Whether streaming messages are supported.

    Returns:
        A spec-compliant Agent Card dict.
    """
    in_modes = default_input_modes or ["text/plain"]
    out_modes = default_output_modes or ["text/plain"]
    return {
        "name": name,
        "description": description,
        "url": url,
        "version": version,
        "protocolVersion": "0.3.0",
        "preferredTransport": "JSONRPC",
        "capabilities": {
            "streaming": streaming,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": in_modes,
        "defaultOutputModes": out_modes,
        "skills": [
            {
                "id": "chat",
                "name": "Chat",
                "description": description,
                "tags": ["chat"],
                "inputModes": in_modes,
                "outputModes": out_modes,
            }
        ],
    }


# ---------------------------------------------------------------------------
# A2A message / task helpers
# ---------------------------------------------------------------------------


def extract_text_from_parts(parts: list[dict[str, Any]]) -> str:
    """Extract all text segments from an A2A ``parts`` list.

    Accepts parts with ``kind=="text"`` (current A2A spec) and
    ``type=="text"`` (legacy).  Raises ``ValueError`` if no text is found.
    """
    fragments: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind") or part.get("type") or ""
        if kind != "text":
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            fragments.append(text)
    if not fragments:
        raise ValueError("No text content found in A2A message parts.")
    return "\n".join(fragments)


def make_text_part(text: str) -> dict[str, Any]:
    """Build a single text part for an A2A message."""
    return {"kind": "text", "text": text}


def make_message(
    *,
    role: str,
    text: str,
    message_id: str,
    context_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Build a standard A2A ``Message`` dict.

    Args:
        role: ``"user"``, ``"agent"``, etc.
        text: Plain-text message body.
        message_id: Unique message identifier.
        context_id: Optional conversation context id.
        task_id: Optional task id the message belongs to.

    Returns:
        A spec-compliant A2A Message dict.
    """
    msg: dict[str, Any] = {
        "role": role,
        "parts": [make_text_part(text)],
        "messageId": message_id,
        "kind": "message",
    }
    if context_id is not None:
        msg["contextId"] = context_id
    if task_id is not None:
        msg["taskId"] = task_id
    return msg


def make_task(
    *,
    task_id: str,
    context_id: str,
    state: str,
    agent_text: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a standard A2A ``Task`` dict.

    Args:
        task_id: Unique task identifier.
        context_id: Conversation context id.
        state: One of the ``STATE_*`` constants.
        agent_text: Final agent response text.
        history: Message history (user + agent messages).

    Returns:
        A spec-compliant A2A Task dict.
    """
    return {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": state,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        "artifacts": [
            {
                "artifactId": uuid.uuid4().hex,
                "name": "response",
                "parts": [make_text_part(agent_text)],
            }
        ],
        "history": history or [],
        "kind": "task",
    }


# ---------------------------------------------------------------------------
# Streaming event builders
# ---------------------------------------------------------------------------


def make_status_update_event(
    *,
    task_id: str,
    context_id: str,
    state: str,
    final: bool,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a ``TaskStatusUpdateEvent`` for SSE streaming.

    Args:
        task_id: The task being updated.
        context_id: Conversation context id.
        state: One of the ``STATE_*`` constants.
        final: Whether this is the terminal status update.
        timestamp: ISO-8601 timestamp; defaults to now.

    Returns:
        A spec-compliant TaskStatusUpdateEvent dict.
    """
    ts = timestamp if timestamp is not None else datetime.now(UTC).isoformat()
    return {
        "taskId": task_id,
        "contextId": context_id,
        "kind": "status-update",
        "status": {"state": state, "timestamp": ts},
        "final": final,
    }


def make_artifact_update_event(
    *,
    task_id: str,
    context_id: str,
    artifact_id: str,
    text: str,
    append: bool,
    last_chunk: bool,
    name: str = "response",
) -> dict[str, Any]:
    """Build a ``TaskArtifactUpdateEvent`` for SSE streaming.

    Args:
        task_id: The task being updated.
        context_id: Conversation context id.
        artifact_id: Stable artifact id across all chunks of this task.
        text: The text delta for this chunk.
        append: ``False`` for the first chunk, ``True`` thereafter.
        last_chunk: ``True`` for the final streamed chunk.
        name: Artifact name (default ``"response"``).

    Returns:
        A spec-compliant TaskArtifactUpdateEvent dict.
    """
    return {
        "taskId": task_id,
        "contextId": context_id,
        "kind": "artifact-update",
        "artifact": {
            "artifactId": artifact_id,
            "name": name,
            "parts": [make_text_part(text)],
        },
        "append": append,
        "lastChunk": last_chunk,
    }


def make_initial_task(
    *,
    task_id: str,
    context_id: str,
    state: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a ``Task`` dict with an empty artifacts list.

    Used as the first SSE frame in ``message/stream`` responses.

    Args:
        task_id: Unique task identifier.
        context_id: Conversation context id.
        state: One of the ``STATE_*`` constants (typically ``"working"``).
        history: Message history (user echo, etc.).

    Returns:
        A Task dict with an empty ``artifacts`` list.
    """
    return {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": state,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        "artifacts": [],
        "history": history or [],
        "kind": "task",
    }
