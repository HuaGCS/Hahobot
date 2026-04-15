"""Runtime checkpoint and pending-turn recovery helpers for AgentLoop."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.session.manager import Session


class CheckpointRuntimeManager:
    """Own runtime checkpoint persistence and crash-recovery state restoration."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    def set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata."""
        session.metadata[self.loop._RUNTIME_CHECKPOINT_KEY] = payload
        self.loop.sessions.save(session)

    def mark_pending_user_turn(self, session: Session) -> None:
        """Mark that the current session has only the triggering user turn persisted."""
        session.metadata[self.loop._PENDING_USER_TURN_KEY] = True

    def clear_pending_user_turn(self, session: Session) -> None:
        """Remove the pending-user-turn marker from session metadata."""
        session.metadata.pop(self.loop._PENDING_USER_TURN_KEY, None)

    def clear_runtime_checkpoint(self, session: Session) -> None:
        """Remove the in-flight runtime checkpoint from session metadata."""
        session.metadata.pop(self.loop._RUNTIME_CHECKPOINT_KEY, None)

    @staticmethod
    def checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        """Build a stable comparison key for checkpoint-message dedupe."""
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
            message.get("reasoning_content"),
            message.get("thinking_blocks"),
        )

    @staticmethod
    def _copy_with_timestamp(message: dict[str, Any]) -> dict[str, Any]:
        """Clone one restored message and ensure it has a timestamp."""
        restored = dict(message)
        restored.setdefault("timestamp", datetime.now().isoformat())
        return restored

    @staticmethod
    def _pending_tool_message(tool_call: dict[str, Any]) -> dict[str, Any]:
        """Materialize one unfinished tool call into an interrupted tool result."""
        tool_id = tool_call.get("id")
        name = ((tool_call.get("function") or {}).get("name")) or "tool"
        return {
            "role": "tool",
            "tool_call_id": tool_id,
            "name": name,
            "content": "Error: Task interrupted before this tool finished.",
            "timestamp": datetime.now().isoformat(),
        }

    def restore_runtime_checkpoint(self, session: Session) -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        checkpoint = session.metadata.get(self.loop._RUNTIME_CHECKPOINT_KEY)
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []

        restored_messages: list[dict[str, Any]] = []
        if isinstance(assistant_message, dict):
            restored_messages.append(self._copy_with_timestamp(assistant_message))
        for message in completed_tool_results:
            if isinstance(message, dict):
                restored_messages.append(self._copy_with_timestamp(message))
        for tool_call in pending_tool_calls:
            if isinstance(tool_call, dict):
                restored_messages.append(self._pending_tool_message(tool_call))

        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                self.checkpoint_message_key(left) == self.checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        session.messages.extend(restored_messages[overlap:])

        self.clear_pending_user_turn(session)
        self.clear_runtime_checkpoint(session)
        return True

    def restore_pending_user_turn(self, session: Session) -> bool:
        """Close a turn that only persisted the user message before crashing."""
        if not session.metadata.get(self.loop._PENDING_USER_TURN_KEY):
            return False

        if session.messages and session.messages[-1].get("role") == "user":
            session.messages.append({
                "role": "assistant",
                "content": "Error: Task interrupted before a response was generated.",
                "timestamp": datetime.now().isoformat(),
            })
            session.updated_at = datetime.now()

        self.clear_pending_user_turn(session)
        return True
