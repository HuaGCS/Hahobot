"""Helpers for listing, inspecting, and resuming saved sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hahobot.session.manager import SessionManager
from hahobot.utils.helpers import ensure_dir, safe_filename

_INTERNAL_SESSION_PREFIXES = ("cron:", "api:", "system:")
_INTERNAL_SESSION_KEYS = {"heartbeat", "dream"}


@dataclass(frozen=True)
class SessionSummary:
    """Serializable session summary for CLI inspection."""

    key: str
    created_at: str | None
    updated_at: str | None
    path: Path
    message_count: int
    persona: str | None
    last_role: str | None
    preview: str
    internal: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "path": str(self.path),
            "message_count": self.message_count,
            "persona": self.persona,
            "last_role": self.last_role,
            "preview": self.preview,
            "internal": self.internal,
        }


@dataclass(frozen=True)
class SessionMessageSummary:
    """Compact rendering of one saved message."""

    role: str
    timestamp: str | None
    content: str
    tool_call_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "timestamp": self.timestamp,
            "content": self.content,
            "tool_call_count": self.tool_call_count,
        }


@dataclass(frozen=True)
class SessionDetail:
    """Serializable detail view for one saved session."""

    key: str
    created_at: str | None
    updated_at: str | None
    path: Path
    message_count: int
    persona: str | None
    metadata: dict[str, Any]
    internal: bool
    shown_limit: int
    messages: tuple[SessionMessageSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "path": str(self.path),
            "message_count": self.message_count,
            "persona": self.persona,
            "metadata": self.metadata,
            "internal": self.internal,
            "shown_limit": self.shown_limit,
            "messages": [message.to_dict() for message in self.messages],
        }


@dataclass(frozen=True)
class SessionExport:
    """Serializable full-fidelity session export payload."""

    key: str
    created_at: str | None
    updated_at: str | None
    path: Path
    message_count: int
    persona: str | None
    metadata: dict[str, Any]
    internal: bool
    messages: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "path": str(self.path),
            "message_count": self.message_count,
            "persona": self.persona,
            "metadata": self.metadata,
            "internal": self.internal,
            "messages": [dict(message) for message in self.messages],
        }


def is_internal_session_key(key: str) -> bool:
    """Return True for scheduler/system sessions that should stay hidden by default."""
    return key in _INTERNAL_SESSION_KEYS or key.startswith(_INTERNAL_SESSION_PREFIXES)


def is_cli_session_key(key: str) -> bool:
    """Return True for sessions created from the local CLI surface."""
    return not is_internal_session_key(key) and (key.startswith("cli:") or ":" not in key)


def _trim_preview(content: str, limit: int = 80) -> str:
    normalized = " ".join((content or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min


def _tool_call_count(message: dict[str, Any]) -> int:
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        return len(tool_calls)
    return 0


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    count = _tool_call_count(message)
    if count:
        suffix = "s" if count != 1 else ""
        return f"[tool call{suffix}: {count}]"
    return ""


def list_session_summaries(
    manager: SessionManager,
    *,
    include_internal: bool = False,
    cli_only: bool = False,
    limit: int | None = None,
) -> list[SessionSummary]:
    """Return recent sessions with lightweight metadata and a short preview."""
    base_items = manager.list_sessions()
    summaries: list[SessionSummary] = []

    for item in base_items:
        key = str(item.get("key") or "")
        if not key:
            continue
        internal = is_internal_session_key(key)
        if internal and not include_internal:
            continue
        if cli_only and not is_cli_session_key(key):
            continue

        session = manager.get_or_create(key)
        last_message = next(
            (
                message
                for message in reversed(session.messages)
                if isinstance(message.get("content"), str) and message.get("content", "").strip()
            ),
            None,
        )
        summaries.append(
            SessionSummary(
                key=key,
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
                path=Path(str(item.get("path") or "")),
                message_count=len(session.messages),
                persona=session.metadata.get("persona"),
                last_role=last_message.get("role") if last_message else None,
                preview=_trim_preview(last_message.get("content", "")) if last_message else "",
                internal=internal,
            )
        )

    summaries.sort(key=lambda item: _parse_dt(item.updated_at), reverse=True)
    if limit is not None:
        return summaries[: max(limit, 0)]
    return summaries


def pick_recent_cli_session_key(manager: SessionManager) -> str | None:
    """Return the most recent local CLI session key, if any."""
    sessions = list_session_summaries(manager, cli_only=True, limit=1)
    if sessions:
        return sessions[0].key
    return None


def load_session_detail(
    manager: SessionManager,
    key: str,
    *,
    limit: int = 20,
) -> SessionDetail | None:
    """Load one saved session detail view by exact key."""
    path_by_key = {
        str(item.get("key") or ""): Path(str(item.get("path") or ""))
        for item in manager.list_sessions()
        if item.get("key")
    }
    if key not in path_by_key:
        return None

    session = manager.get_or_create(key)
    selected_messages = session.messages[-max(limit, 0):] if limit >= 0 else list(session.messages)
    detail_messages = tuple(
        SessionMessageSummary(
            role=str(message.get("role") or "unknown"),
            timestamp=message.get("timestamp"),
            content=_trim_preview(_message_text(message), limit=240),
            tool_call_count=_tool_call_count(message),
        )
        for message in selected_messages
    )

    created_at = None
    updated_at = None
    for item in manager.list_sessions():
        if item.get("key") == key:
            created_at = item.get("created_at")
            updated_at = item.get("updated_at")
            break

    return SessionDetail(
        key=key,
        created_at=created_at,
        updated_at=updated_at,
        path=path_by_key[key],
        message_count=len(session.messages),
        persona=session.metadata.get("persona"),
        metadata=dict(session.metadata),
        internal=is_internal_session_key(key),
        shown_limit=max(limit, 0),
        messages=detail_messages,
    )


def load_session_export(
    manager: SessionManager,
    key: str,
) -> SessionExport | None:
    """Load one saved session with full message payloads for export."""
    path_by_key = {
        str(item.get("key") or ""): Path(str(item.get("path") or ""))
        for item in manager.list_sessions()
        if item.get("key")
    }
    if key not in path_by_key:
        return None

    session = manager.get_or_create(key)
    created_at = None
    updated_at = None
    for item in manager.list_sessions():
        if item.get("key") == key:
            created_at = item.get("created_at")
            updated_at = item.get("updated_at")
            break

    return SessionExport(
        key=key,
        created_at=created_at,
        updated_at=updated_at,
        path=path_by_key[key],
        message_count=len(session.messages),
        persona=session.metadata.get("persona"),
        metadata=dict(session.metadata),
        internal=is_internal_session_key(key),
        messages=tuple(dict(message) for message in session.messages),
    )


def render_session_list_text(
    sessions: list[SessionSummary],
    *,
    cli_only: bool = False,
    include_internal: bool = False,
) -> str:
    """Render a compact human-readable session list."""
    title = "hahobot sessions list"
    if cli_only:
        title += " --cli-only"
    elif include_internal:
        title += " --all"

    lines = [title, ""]
    if not sessions:
        lines.append("No sessions found.")
        return "\n".join(lines)

    for session in sessions:
        stamp = session.updated_at or session.created_at or "unknown-time"
        suffix: list[str] = [stamp]
        suffix.append(f"{session.message_count} msg")
        if session.persona:
            suffix.append(f"persona={session.persona}")
        if session.internal:
            suffix.append("internal")
        lines.append(f"- {session.key} ({', '.join(suffix)})")
        if session.preview:
            role = session.last_role or "message"
            lines.append(f"  {role}: {session.preview}")
    return "\n".join(lines)


def render_session_detail_text(detail: SessionDetail) -> str:
    """Render one session with metadata and recent messages."""
    lines = [
        f"hahobot sessions show {detail.key}",
        "",
        f"Path: {detail.path}",
        f"Created: {detail.created_at or 'unknown'}",
        f"Updated: {detail.updated_at or 'unknown'}",
        f"Messages: {detail.message_count}",
        f"Persona: {detail.persona or 'default'}",
        f"Internal: {'yes' if detail.internal else 'no'}",
    ]
    if detail.metadata:
        lines.append(f"Metadata: {detail.metadata}")
    lines.append("")
    if detail.messages:
        shown = min(detail.shown_limit, detail.message_count)
        lines.append(f"Recent messages (showing last {shown}):")
        for message in detail.messages:
            stamp = message.timestamp or "unknown-time"
            lines.append(f"- {message.role} @ {stamp}")
            if message.content:
                lines.append(f"  {message.content}")
            if message.tool_call_count:
                lines.append(f"  tool_calls: {message.tool_call_count}")
    else:
        lines.append("No messages saved in this session.")
    return "\n".join(lines)


def default_session_export_path(workspace: Path, key: str, export_format: str) -> Path:
    """Return the default output path for one exported session artifact."""
    suffix = ".md" if export_format == "md" else ".json"
    out_dir = ensure_dir(workspace / "out" / "sessions")
    filename = safe_filename(key.replace(":", "_")) or "session"
    return out_dir / f"{filename}{suffix}"


def render_session_export_markdown(data: SessionExport) -> str:
    """Render one full session export as human-readable Markdown."""
    lines = [
        f"# Session Export: {data.key}",
        "",
        f"- Source Path: `{data.path}`",
        f"- Created: {data.created_at or 'unknown'}",
        f"- Updated: {data.updated_at or 'unknown'}",
        f"- Messages: {data.message_count}",
        f"- Persona: {data.persona or 'default'}",
        f"- Internal: {'yes' if data.internal else 'no'}",
    ]

    if data.metadata:
        lines.extend(
            [
                "",
                "## Metadata",
                "",
                "```json",
                json.dumps(data.metadata, ensure_ascii=False, indent=2),
                "```",
            ]
        )

    lines.extend(["", "## Transcript"])
    if not data.messages:
        lines.extend(["", "_No messages saved in this session._"])
        return "\n".join(lines)

    for index, message in enumerate(data.messages, start=1):
        role = str(message.get("role") or "unknown")
        timestamp = str(message.get("timestamp") or "unknown-time")
        lines.extend(["", f"### {index}. {role} @ {timestamp}", ""])
        content = message.get("content")
        if isinstance(content, str):
            lines.extend(["```text", content, "```"])
        else:
            lines.extend(
                [
                    "```json",
                    json.dumps(content, ensure_ascii=False, indent=2),
                    "```",
                ]
            )
        extras = {
            key: value
            for key, value in message.items()
            if key not in {"role", "content", "timestamp"}
        }
        if extras:
            lines.extend(
                [
                    "",
                    "Extra fields:",
                    "```json",
                    json.dumps(extras, ensure_ascii=False, indent=2),
                    "```",
                ]
            )
    return "\n".join(lines)


def export_session_artifact(
    data: SessionExport,
    *,
    workspace: Path,
    export_format: str,
    output_path: Path | None = None,
) -> Path:
    """Write one session export artifact and return the output path."""
    target = (
        output_path.expanduser().resolve()
        if output_path is not None
        else default_session_export_path(workspace, data.key, export_format)
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if export_format == "md":
        target.write_text(render_session_export_markdown(data), encoding="utf-8")
        return target

    target.write_text(
        json.dumps(data.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
