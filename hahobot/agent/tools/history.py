"""Tools for searching and expanding archived chat history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hahobot.agent.history_archive import HistoryArchiveStore, content_to_text
from hahobot.agent.tools.base import Tool


class _HistoryTool(Tool):
    """Shared persona/session-bound archive tool behavior."""

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._channel = ""
        self._chat_id = ""
        self._persona: str | None = None

    def update_workspace(self, workspace: Path) -> None:
        self._workspace = workspace

    def set_context(self, channel: str, chat_id: str, persona: str | None = None) -> None:
        self._channel = channel
        self._chat_id = chat_id
        self._persona = persona

    def _store(self) -> HistoryArchiveStore:
        return HistoryArchiveStore(self._workspace, self._persona)

    def _current_session_key(self) -> str | None:
        if not self._channel or not self._chat_id:
            return None
        return f"{self._channel}:{self._chat_id}"


class HistorySearchTool(_HistoryTool):
    """Find archived history chunks for the active persona."""

    @property
    def name(self) -> str:
        return "history_search"

    @property
    def description(self) -> str:
        return (
            "Search archived chat-history chunks for the active persona. "
            "Returns compact observation ids, titles, summaries, files, concepts, and time ranges. "
            "Use history_timeline for surrounding context, then history_expand with a returned id "
            "when you need transcript details."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for in archived history.",
                    "minLength": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of archive hits to return (1-10).",
                    "minimum": 1,
                    "maximum": 10,
                },
                "sessionKey": {
                    "type": ["string", "null"],
                    "description": "Optional exact session key filter, e.g. 'cli:direct'.",
                },
                "since": {
                    "type": ["string", "null"],
                    "description": "Optional lower time bound, such as '2026-03-01' or ISO datetime.",
                },
                "until": {
                    "type": ["string", "null"],
                    "description": "Optional upper time bound, such as '2026-03-30' or ISO datetime.",
                },
                "file": {
                    "type": ["string", "null"],
                    "description": "Optional file/path filter, e.g. 'hahobot/agent/loop.py'.",
                },
                "type": {
                    "type": ["string", "null"],
                    "description": "Optional observation type filter such as bugfix, decision, feature, refactor.",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        limit: int = 5,
        session_key: str | None = None,
        since: str | None = None,
        until: str | None = None,
        file: str | None = None,
        type: str | None = None,
        **kwargs: Any,
    ) -> str:
        if session_key is None:
            session_key = kwargs.get("sessionKey")
        if file is None:
            file = kwargs.get("path") or kwargs.get("filePath")
        if type is None:
            type = kwargs.get("observationType")
        results = self._store().search(
            query=query,
            limit=limit,
            session_key=session_key,
            preferred_session_key=None if session_key else self._current_session_key(),
            since=since,
            until=until,
            file=file,
            observation_type=type,
        )
        if not results:
            return f'No archived history matches for "{query}".'

        lines = [f'Archived history matches for "{query}":\n']
        for idx, entry in enumerate(results, 1):
            lines.append(f'{idx}. ID: {entry.get("id", "")}')
            lines.append(
                "   Session: "
                f'{entry.get("sessionKey", "")} | '
                f'{entry.get("timeStart", "")} -> {entry.get("timeEnd", "")}'
            )
            summary = str(entry.get("summary", "")).strip()
            if summary:
                lines.append(f"   Summary: {summary}")
            observation_type = str(entry.get("observationType", "")).strip()
            if observation_type:
                lines.append(f"   Type: {observation_type}")
            files = [str(item) for item in entry.get("files") or [] if str(item).strip()]
            if files:
                lines.append(f"   Files: {', '.join(files[:6])}")
            concepts = [str(item) for item in entry.get("concepts") or [] if str(item).strip()]
            if concepts:
                lines.append(f"   Concepts: {', '.join(concepts[:8])}")
            keywords = [str(item) for item in entry.get("keywords") or [] if str(item).strip()]
            if keywords:
                lines.append(f"   Keywords: {', '.join(keywords[:8])}")
            lines.append(
                f'   Next: call history_timeline anchor="{entry.get("id", "")}" '
                f'or history_expand id="{entry.get("id", "")}"'
            )
        return "\n".join(lines)


class HistoryTimelineTool(_HistoryTool):
    """Return a compact timeline around a query, file, or archive id."""

    @property
    def name(self) -> str:
        return "history_timeline"

    @property
    def description(self) -> str:
        return (
            "Show a compact archived-memory timeline for the active persona by query, file/path, "
            "or anchor archive id. Use this after history_search and before history_expand when "
            "you need chronological context without full transcripts."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": ["string", "null"], "description": "Optional text query."},
                "file": {"type": ["string", "null"], "description": "Optional file/path filter."},
                "anchor": {"type": ["string", "null"], "description": "Optional archive id to center on."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
        }

    async def execute(
        self,
        query: str | None = None,
        file: str | None = None,
        anchor: str | None = None,
        limit: int = 8,
        **kwargs: Any,
    ) -> str:
        if file is None:
            file = kwargs.get("path") or kwargs.get("filePath")
        store = self._store()
        anchor_entry = None
        if anchor:
            loaded = store.load_entry(anchor)
            if isinstance(loaded, dict) and isinstance(loaded.get("entry"), dict):
                anchor_entry = loaded["entry"]

        search_query = query or file or ""
        if anchor_entry and not search_query:
            concepts = anchor_entry.get("concepts") or anchor_entry.get("keywords") or []
            search_query = " ".join(str(item) for item in concepts[:3]) or str(anchor_entry.get("title", ""))

        entries = store.search(
            query=search_query,
            limit=max(1, min(limit, 20)),
            preferred_session_key=self._current_session_key(),
            file=file,
        )
        if anchor_entry and all(entry.get("id") != anchor_entry.get("id") for entry in entries):
            entries = [anchor_entry, *entries]
        entries = sorted(entries[: max(1, min(limit, 20))], key=lambda item: str(item.get("timeEnd", "")))
        if not entries:
            return "No archived history timeline entries found."

        lines = ["Archived history timeline:"]
        for entry in entries:
            marker = "*" if anchor and entry.get("id") == anchor else "-"
            title = str(entry.get("title") or entry.get("summary") or "(untitled)").strip()
            lines.append(
                f'{marker} {entry.get("timeEnd", "")} | {entry.get("observationType", "conversation")} | '
                f'{title[:120]} | id={entry.get("id", "")} '
            )
            files = [str(item) for item in entry.get("files") or [] if str(item).strip()]
            if files:
                lines.append(f"  files: {', '.join(files[:5])}")
            facts = [str(item) for item in entry.get("facts") or [] if str(item).strip()]
            if facts:
                lines.append(f"  facts: {'; '.join(facts[:2])}")
        lines.append("Next: use history_expand id=<id> for transcript details.")
        return "\n".join(lines)


class HistoryExpandTool(_HistoryTool):
    """Return a transcript view for one archived history chunk."""

    @property
    def name(self) -> str:
        return "history_expand"

    @property
    def description(self) -> str:
        return (
            "Expand one archived history chunk by id and return a compact transcript "
            "for the active persona."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Archive id returned by history_search.",
                    "minLength": 1,
                },
                "maxMessages": {
                    "type": "integer",
                    "description": (
                        "Maximum number of transcript messages to show. When the chunk is longer, "
                        "the tool shows the beginning and end with an omission marker."
                    ),
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["id"],
        }

    async def execute(self, id: str, max_messages: int = 20, **kwargs: Any) -> str:
        if "maxMessages" in kwargs and "max_messages" not in kwargs:
            max_messages = kwargs["maxMessages"]
        record = self._store().load_entry(id)
        if record is None:
            return f'Error: Archived history id not found: "{id}"'
        if isinstance(record, dict) and record.get("error"):
            return f'Error: {record["error"]}'

        entry = record.get("entry") if isinstance(record, dict) else None
        messages = record.get("messages") if isinstance(record, dict) else None
        if not isinstance(entry, dict) or not isinstance(messages, list):
            return f'Error: Archived history payload is invalid for id "{id}"'

        selected, omitted = self._select_messages(messages, max_messages)
        lines = [f'Archived transcript for "{id}":']
        lines.append(
            f'Session: {entry.get("sessionKey", "")} | '
            f'{entry.get("timeStart", "")} -> {entry.get("timeEnd", "")}'
        )
        summary = str(entry.get("summary", "")).strip()
        if summary:
            lines.append(f"Summary: {summary}")
        lines.append("")

        if omitted > 0:
            lines.append(
                f"[showing {len(selected) - 1} of {len(messages)} messages; "
                f"{omitted} omitted in the middle]"
            )

        for message in selected:
            if message is None:
                lines.append(f"... [{omitted} messages omitted] ...")
                continue
            role = str(message.get("role", "?")).upper()
            timestamp = str(message.get("timestamp", ""))[:16]
            name = str(message.get("name", "")).strip()
            label = f"{role}({name})" if name and role == "TOOL" else role
            content = content_to_text(message.get("content")).strip() or "(no content)"
            lines.append(f"[{timestamp}] {label}: {content}")
            if message.get("tool_calls"):
                lines.append(f"   tool_calls: {message.get('tool_calls')}")
        return "\n".join(lines)

    @staticmethod
    def _select_messages(
        messages: list[dict[str, Any]],
        max_messages: int,
    ) -> tuple[list[dict[str, Any] | None], int]:
        if max_messages <= 0 or len(messages) <= max_messages:
            return list(messages), 0
        if max_messages < 4:
            selected = list(messages[-max_messages:])
            return selected, len(messages) - len(selected)

        head = max_messages // 2
        tail = max_messages - head
        omitted = max(0, len(messages) - head - tail)
        return [*messages[:head], None, *messages[-tail:]], omitted
