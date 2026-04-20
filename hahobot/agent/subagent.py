"""Subagent manager for background task execution."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from hahobot.agent.hook import AgentHook, AgentHookContext
from hahobot.agent.runner import AgentRunner, AgentRunSpec
from hahobot.agent.skills import BUILTIN_SKILLS_DIR
from hahobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from hahobot.agent.tools.notebook import NotebookEditTool
from hahobot.agent.tools.policy import RuntimeToolPolicy
from hahobot.agent.tools.registry import ToolRegistry
from hahobot.agent.tools.search import GlobTool, GrepTool
from hahobot.agent.tools.shell import ExecTool
from hahobot.agent.tools.web import WebFetchTool, WebSearchTool
from hahobot.bus.events import InboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.config.schema import ExecToolConfig
from hahobot.providers.base import LLMProvider
from hahobot.utils.prompt_templates import render_template


class _SubagentHook(AgentHook):
    """Logging-only hook for subagent execution."""

    def __init__(self, task_id: str) -> None:
        self._task_id = task_id

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        model: str | None = None,
        brave_api_key: str | None = None,
        web_proxy: str | None = None,
        web_enabled: bool = True,
        web_search_provider: str = "brave",
        web_search_base_url: str | None = None,
        web_search_max_results: int = 5,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
    ):
        from hahobot.config.schema import ExecToolConfig
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.max_tool_result_chars = max_tool_result_chars
        self.brave_api_key = brave_api_key
        self.web_proxy = web_proxy
        self.web_enabled = web_enabled
        self.web_search_provider = web_search_provider
        self.web_search_base_url = web_search_base_url
        self.web_search_max_results = web_search_max_results
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self.runner = AgentRunner(provider)
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        self._task_meta: dict[str, dict[str, str]] = {}

    def apply_runtime_config(
        self,
        *,
        workspace: Path,
        model: str,
        brave_api_key: str | None,
        web_proxy: str | None,
        web_enabled: bool,
        web_search_provider: str,
        web_search_base_url: str | None,
        web_search_max_results: int,
        exec_config: ExecToolConfig,
        restrict_to_workspace: bool,
        disabled_skills: list[str],
    ) -> None:
        """Update runtime-configurable settings for future subagent tasks."""
        self.workspace = workspace
        self.model = model
        self.brave_api_key = brave_api_key
        self.web_proxy = web_proxy
        self.web_enabled = web_enabled
        self.web_search_provider = web_search_provider
        self.web_search_base_url = web_search_base_url
        self.web_search_max_results = web_search_max_results
        self.exec_config = exec_config
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills)

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        mode: str = "implement",
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}
        normalized_mode = self._normalize_mode(mode)
        effective_session_key = session_key or f"{origin_channel}:{origin_chat_id}"
        self._task_meta[task_id] = {
            "task_id": task_id,
            "label": display_label,
            "mode": normalized_mode,
            "origin_channel": origin_channel,
            "origin_chat_id": origin_chat_id,
            "session_key": effective_session_key,
        }

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, normalized_mode, origin)
        )
        self._running_tasks[task_id] = bg_task
        if effective_session_key:
            self._session_tasks.setdefault(effective_session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_meta.pop(task_id, None)
            if effective_session_key and (ids := self._session_tasks.get(effective_session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[effective_session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}] [{}]: {}", task_id, normalized_mode, display_label)
        return (
            f"Subagent [{display_label}] started in {normalized_mode} mode (id: {task_id}). "
            "I'll notify you when it completes."
        )

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        mode: str,
        origin: dict[str, str],
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task [{}]: {}", task_id, mode, label)

        try:
            tools = self._build_tools_for_mode(mode)
            system_prompt = self._build_subagent_prompt(mode)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            result = await self.runner.run(AgentRunSpec(
                initial_messages=messages,
                tools=tools,
                model=self.model,
                max_iterations=15,
                max_tool_result_chars=self.max_tool_result_chars,
                hook=_SubagentHook(task_id),
                max_iterations_message="Task completed but no final response was generated.",
                error_message=None,
                fail_on_tool_error=True,
            ))
            if result.stop_reason == "tool_error":
                await self._announce_result(
                    task_id,
                    label,
                    task,
                    self._format_partial_progress(result),
                    origin,
                    "error",
                )
                return
            if result.stop_reason == "error":
                await self._announce_result(
                    task_id,
                    label,
                    task,
                    result.error or "Error: subagent execution failed.",
                    origin,
                    "error",
                )
                return
            final_result = result.final_content or "Task completed but no final response was generated."

            logger.info("Subagent [{}] completed successfully", task_id)
            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(task_id, label, task, error_msg, origin, "error")

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
        )

        # Inject as system message to trigger main agent
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            metadata={
                "injected_event": "subagent_result",
                "subagent_task_id": task_id,
                "subagent_status": status,
                "subagent_label": label,
            },
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    @staticmethod
    def _format_partial_progress(result) -> str:
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error and not failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")

    @staticmethod
    def _normalize_mode(mode: str | None) -> str:
        normalized = (mode or "implement").strip().lower()
        if normalized in {"explore", "verify"}:
            return normalized
        return "implement"

    def _build_tools_for_mode(self, mode: str) -> ToolRegistry:
        """Build one subagent tool registry for the selected mode."""
        tools = ToolRegistry()
        from hahobot.config.schema import ImageGenConfig, WebSearchConfig, WebToolsConfig

        policy = RuntimeToolPolicy(
            workspace=self.workspace,
            restrict_to_workspace=self.restrict_to_workspace,
            web_config=WebToolsConfig(
                enable=self.web_enabled,
                proxy=self.web_proxy,
                search=WebSearchConfig(
                    provider=self.web_search_provider,
                    api_key=self.brave_api_key or "",
                    base_url=self.web_search_base_url or "",
                    max_results=self.web_search_max_results,
                ),
            ),
            exec_config=self.exec_config,
            image_gen_config=ImageGenConfig(),
            builtin_read_dirs=(BUILTIN_SKILLS_DIR,),
        )
        scope = policy.workspace_scope()
        allowed_dir = scope.allowed_dir
        extra_read = list(scope.extra_read_dirs) if scope.extra_read_dirs else None

        tools.register(
            ReadFileTool(
                workspace=self.workspace,
                allowed_dir=allowed_dir,
                extra_allowed_dirs=extra_read,
            )
        )
        tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(GlobTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(GrepTool(workspace=self.workspace, allowed_dir=allowed_dir))

        if mode == "implement":
            tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(NotebookEditTool(workspace=self.workspace, allowed_dir=allowed_dir))

        if mode in {"implement", "verify"} and policy.exec().enabled:
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                sandbox=self.exec_config.sandbox,
                path_append=self.exec_config.path_append,
                allowed_env_keys=self.exec_config.allowed_env_keys,
            ))

        if policy.web().enabled:
            tools.register(
                WebSearchTool(
                    provider=self.web_search_provider,
                    api_key=self.brave_api_key,
                    base_url=self.web_search_base_url,
                    max_results=self.web_search_max_results,
                    proxy=self.web_proxy,
                )
            )
            tools.register(WebFetchTool(proxy=self.web_proxy))

        return tools

    def _build_subagent_prompt(self, mode: str) -> str:
        """Build a focused system prompt for the subagent."""
        from hahobot.agent.context import ContextBuilder
        from hahobot.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        skills_summary = SkillsLoader(
            self.workspace,
            disabled_skills=set(self.disabled_skills),
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            time_ctx=time_ctx,
            mode=mode,
            workspace=str(self.workspace),
            skills_summary=skills_summary or "",
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)

    def running_tasks_snapshot(self, session_key: str | None = None) -> list[dict[str, str]]:
        """Return a stable snapshot of currently running subagent tasks."""
        snapshot: list[dict[str, str]] = []
        for task_id, task in self._running_tasks.items():
            if task.done():
                continue
            meta = dict(self._task_meta.get(task_id) or {})
            meta.setdefault("task_id", task_id)
            if session_key and meta.get("session_key") != session_key:
                continue
            snapshot.append(meta)
        return sorted(snapshot, key=lambda item: item.get("task_id", ""))
