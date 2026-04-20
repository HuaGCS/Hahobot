"""Read-only runtime self-inspection tool for the active agent session."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from hahobot import __version__
from hahobot.agent.tools.base import Tool, tool_parameters
from hahobot.agent.tools.schema import StringSchema, tool_parameters_schema
from hahobot.agent.working_checkpoint import normalize_working_checkpoint

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop


@tool_parameters(
    tool_parameters_schema(
        section=StringSchema(
            "Optional section to inspect. Defaults to 'all'.",
            enum=("all", "runtime", "session", "tools", "subagents"),
        )
    )
)
class SelfInspectTool(Tool):
    """Expose a compact, read-only snapshot of runtime state."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop
        self._channel = ""
        self._chat_id = ""
        self._session_key = ""
        self._persona: str | None = None

    def set_context(
        self,
        channel: str,
        chat_id: str,
        session_key: str | None = None,
        persona: str | None = None,
    ) -> None:
        """Bind the current chat/session context for later inspection."""
        self._channel = channel
        self._chat_id = chat_id
        self._session_key = session_key or f"{channel}:{chat_id}"
        self._persona = persona

    @property
    def name(self) -> str:
        return "self_inspect"

    @property
    def description(self) -> str:
        return (
            "Return a read-only JSON snapshot of the current runtime state, including "
            "the active session, registered tools, and running subagents. "
            "Use this when you need to verify your own configuration before taking action."
        )

    @property
    def read_only(self) -> bool:
        return True

    def _provider_name(self) -> str:
        provider_name = getattr(self._loop.provider, "name", None)
        if isinstance(provider_name, str) and provider_name.strip():
            return provider_name
        return type(self._loop.provider).__name__

    def _runtime_snapshot(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "workspace": str(self._loop.workspace),
            "model": self._loop.model,
            "provider": self._provider_name(),
            "provider_retry_mode": self._loop.provider_retry_mode,
            "max_iterations": self._loop.max_iterations,
            "context_window_tokens": self._loop.context_window_tokens,
        }

    def _session_snapshot(self) -> dict[str, Any] | None:
        if not self._session_key:
            return None

        session = self._loop.sessions.get_or_create(self._session_key)
        checkpoint = normalize_working_checkpoint(
            session.metadata.get(self._loop._WORKING_CHECKPOINT_KEY)
        )
        return {
            "key": session.key,
            "channel": self._channel,
            "chat_id": self._chat_id,
            "persona": self._loop._get_session_persona(session) if self._persona is None else self._persona,
            "language": self._loop._get_session_language(session),
            "message_count": len(session.messages),
            "live_message_count": len(session.get_history(max_messages=0)),
            "last_consolidated": session.last_consolidated,
            "pending_user_turn": bool(session.metadata.get(self._loop._PENDING_USER_TURN_KEY)),
            "runtime_checkpoint_present": self._loop._RUNTIME_CHECKPOINT_KEY in session.metadata,
            "working_checkpoint": checkpoint,
        }

    def _tools_snapshot(self) -> dict[str, Any]:
        names = sorted(self._loop.tools.tool_names)
        return {
            "count": len(names),
            "registered": names,
        }

    def _subagents_snapshot(self) -> dict[str, Any]:
        current_session = self._session_key or None
        running = self._loop.subagents.running_tasks_snapshot()
        current_running = self._loop.subagents.running_tasks_snapshot(session_key=current_session)
        return {
            "running_count": len(running),
            "current_session_key": current_session,
            "current_session_running_count": len(current_running),
            "running": running,
            "current_session_running": current_running,
        }

    def _snapshot(self) -> dict[str, Any]:
        return {
            "runtime": self._runtime_snapshot(),
            "session": self._session_snapshot(),
            "tools": self._tools_snapshot(),
            "subagents": self._subagents_snapshot(),
        }

    async def execute(self, section: str = "all", **kwargs: Any) -> str:
        """Return the requested snapshot section as formatted JSON."""
        section = str(kwargs.get("section", section) or "all").strip().lower()
        snapshot = self._snapshot()
        if section == "all":
            target: Any = snapshot
        else:
            target = snapshot.get(section)
            if target is None:
                return (
                    "Error: Unknown section. "
                    "Use one of: all, runtime, session, tools, subagents."
                )
        return json.dumps(target, ensure_ascii=False, indent=2, sort_keys=True)
