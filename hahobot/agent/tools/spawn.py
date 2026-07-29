"""Spawn tool for creating background subagents."""

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from hahobot.agent.tools.base import Tool, tool_parameters
from hahobot.agent.tools.schema import StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from hahobot.agent.subagent import SubagentManager


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema("The task for the subagent to complete"),
        label=StringSchema("Optional short label for the task (for display)"),
        mode=StringSchema(
            "Subagent mode: explore for read-only investigation, implement for bounded changes, verify for independent validation.",
            enum=("explore", "implement", "verify"),
        ),
        model=StringSchema(
            "Optional model selector. Either a role name from agents.defaults.subagent.models "
            "(e.g. 'fast', 'strong', 'reasoning') or a literal provider/model identifier "
            "(e.g. 'openai/gpt-4.1-mini'). Omit to use the active default model."
        ),
        required=["task"],
    )
)
class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._context: ContextVar[tuple[str, str, str, str]] = ContextVar(
            f"spawn_context_{id(self)}",
            default=("cli", "direct", "cli:direct", "user"),
        )

    def set_context(
        self,
        channel: str,
        chat_id: str,
        session_key: str | None = None,
        sender_id: str | None = None,
    ) -> None:
        """Set the origin context for subagent announcements."""
        self._context.set(
            (
                channel,
                chat_id,
                session_key or f"{channel}:{chat_id}",
                sender_id or "user",
            )
        )

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done. "
            "Use mode='explore' for investigation, mode='implement' for bounded edits, "
            "and mode='verify' for independent validation. "
            "Optional model: pass a role name configured under "
            "agents.defaults.subagent.models (e.g. 'fast' for cheap quick tasks, "
            "'strong' for hard reasoning) or omit for the default. "
            "For deliverables or existing projects, inspect the workspace first "
            "and use a dedicated subdirectory when helpful."
        )

    async def execute(
        self,
        task: str,
        label: str | None = None,
        mode: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        origin_channel, origin_chat_id, session_key, sender_id = self._context.get()
        spawn_kwargs: dict[str, Any] = {
            "task": task,
            "label": label,
            "mode": mode or "implement",
            "origin_channel": origin_channel,
            "origin_chat_id": origin_chat_id,
            "session_key": session_key,
            "model": model,
        }
        if sender_id != "user":
            spawn_kwargs["sender_id"] = sender_id
        return await self._manager.spawn(
            **spawn_kwargs,
        )
