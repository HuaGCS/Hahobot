"""Helpers for building compact session-scoped working checkpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from hahobot.agent.hook import AgentHookContext

_TEXT_LIMIT = 220
_STEP_LIMIT = 160
_PREVIEW_LIMIT = 240


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _trim_text(value: str | None, *, limit: int) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _content_preview(value: Any, *, limit: int = _PREVIEW_LIMIT) -> str:
    if isinstance(value, str):
        return _trim_text(value, limit=limit)
    if value is None:
        return ""
    try:
        return _trim_text(json.dumps(value, ensure_ascii=False), limit=limit)
    except TypeError:
        return _trim_text(str(value), limit=limit)


def latest_user_goal(messages: list[dict[str, Any]]) -> str:
    """Return a short goal line based on the latest user message."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        preview = _content_preview(message.get("content"), limit=_TEXT_LIMIT)
        if preview:
            return preview
    return ""


def tool_names(tool_calls: list[Any]) -> list[str]:
    """Return normalized tool names from provider requests or serialized payloads."""
    names: list[str] = []
    for call in tool_calls:
        name = ""
        if hasattr(call, "name"):
            name = str(getattr(call, "name", "") or "")
        elif isinstance(call, dict):
            fn = call.get("function")
            if isinstance(fn, dict):
                name = str(fn.get("name") or "")
            if not name:
                name = str(call.get("name") or "")
        if name:
            names.append(name)
    return names


def result_tool_names(results: list[Any]) -> list[str]:
    """Return tool names from serialized tool-result messages."""
    names: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        name = str(result.get("name") or "")
        if name:
            names.append(name)
    return names


def normalize_working_checkpoint(value: Any) -> dict[str, Any] | None:
    """Normalize persisted checkpoint metadata for rendering."""
    if not isinstance(value, dict):
        return None
    goal = _trim_text(str(value.get("goal") or ""), limit=_TEXT_LIMIT)
    status = str(value.get("status") or "").strip() or "pending"
    current_step = _trim_text(str(value.get("current_step") or ""), limit=_STEP_LIMIT)
    next_step = _trim_text(str(value.get("next_step") or ""), limit=_STEP_LIMIT)
    response_preview = _trim_text(str(value.get("response_preview") or ""), limit=_PREVIEW_LIMIT)
    updated_at = str(value.get("updated_at") or "").strip() or None
    tool_list = [str(item) for item in value.get("recent_tools") or [] if str(item).strip()]
    stop_reason = str(value.get("stop_reason") or "").strip() or None
    if not any([goal, current_step, next_step, response_preview, tool_list]):
        return None
    return {
        "goal": goal,
        "status": status,
        "current_step": current_step,
        "next_step": next_step,
        "response_preview": response_preview,
        "updated_at": updated_at,
        "recent_tools": tool_list,
        "stop_reason": stop_reason,
    }


def build_pending_checkpoint(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build the initial queued checkpoint for a newly persisted user turn."""
    goal = latest_user_goal(messages)
    if not goal:
        return None
    return {
        "goal": goal,
        "status": "pending",
        "current_step": "Queued user request",
        "next_step": "Plan the next steps",
        "updated_at": _now_iso(),
        "recent_tools": [],
        "response_preview": "",
        "stop_reason": None,
    }


def build_checkpoint_from_runner_payload(
    messages: list[dict[str, Any]],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a running/completed checkpoint from runner checkpoint payloads."""
    goal = latest_user_goal(messages)
    phase = str(payload.get("phase") or "").strip().lower()
    if not goal or not phase:
        return None

    recent_tools = tool_names(payload.get("pending_tool_calls") or [])
    if not recent_tools:
        recent_tools = result_tool_names(payload.get("completed_tool_results") or [])

    if phase == "awaiting_tools":
        label = ", ".join(recent_tools[:3])
        current_step = (
            f"Running tools: {label}" if label else "Preparing tool execution"
        )
        next_step = "Wait for tool results"
        status = "running"
        response_preview = ""
    elif phase == "tools_completed":
        label = ", ".join(recent_tools[:3])
        current_step = (
            f"Completed tools: {label}" if label else "Completed tool execution"
        )
        next_step = "Prepare the final response"
        status = "running"
        response_preview = ""
    elif phase == "final_response":
        assistant_message = payload.get("assistant_message")
        response_preview = ""
        if isinstance(assistant_message, dict):
            response_preview = _content_preview(assistant_message.get("content"))
        current_step = "Final response ready"
        next_step = ""
        status = "completed"
    else:
        return None

    return {
        "goal": goal,
        "status": status,
        "current_step": _trim_text(current_step, limit=_STEP_LIMIT),
        "next_step": _trim_text(next_step, limit=_STEP_LIMIT),
        "updated_at": _now_iso(),
        "recent_tools": recent_tools[:6],
        "response_preview": response_preview,
        "stop_reason": None,
    }


def build_checkpoint_from_context(context: AgentHookContext) -> dict[str, Any] | None:
    """Build the post-iteration checkpoint from final hook context."""
    goal = latest_user_goal(context.messages)
    if not goal:
        return None

    preview = _content_preview(context.error or context.final_content)
    if context.error or context.stop_reason in {"error", "tool_error", "empty_final_response"}:
        current_step = "Run ended with an error"
        next_step = "Review the failure and retry with a narrower step"
        status = "error"
    elif context.stop_reason == "max_iterations":
        current_step = "Reached the max iteration limit"
        next_step = "Break the task into smaller steps"
        status = "blocked"
    elif context.final_content:
        current_step = "Final response delivered"
        next_step = ""
        status = "completed"
    elif context.tool_calls:
        names = tool_names(context.tool_calls)
        current_step = (
            f"Running tools: {', '.join(names[:3])}" if names else "Running tools"
        )
        next_step = "Wait for tool results"
        status = "running"
    else:
        return None

    tools = [str(event.get("name") or "") for event in context.tool_events or [] if event.get("name")]
    return {
        "goal": goal,
        "status": status,
        "current_step": _trim_text(current_step, limit=_STEP_LIMIT),
        "next_step": _trim_text(next_step, limit=_STEP_LIMIT),
        "updated_at": _now_iso(),
        "recent_tools": tools[:6],
        "response_preview": preview,
        "stop_reason": str(context.stop_reason or "") or None,
    }


def build_interrupted_checkpoint(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build an interruption marker after restoring a crashed turn."""
    goal = latest_user_goal(messages)
    if not goal:
        return None
    return {
        "goal": goal,
        "status": "interrupted",
        "current_step": "Previous run was interrupted",
        "next_step": "Review the restored transcript or retry",
        "updated_at": _now_iso(),
        "recent_tools": [],
        "response_preview": "",
        "stop_reason": "interrupted",
    }
