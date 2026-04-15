"""Agent runner orchestration helpers for AgentLoop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from hahobot.agent.hook import AgentHook
from hahobot.agent.loop_hooks import LoopHookChain, LoopRunHook
from hahobot.agent.runner import AgentRunSpec
from hahobot.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.session.manager import Session


class RunRuntimeManager:
    """Own runner/hook wiring and direct-process entrypoints for one AgentLoop."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    async def run_agent_loop(
        self,
        initial_messages: list[dict[str, Any]],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        session: Session | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        persona: str | None = None,
    ) -> tuple[str | None, list[str], list[dict[str, Any]], str]:
        """Run the main agent iteration loop and normalize result bookkeeping."""
        result = await self.loop.runner.run(self._build_run_spec(
            initial_messages=initial_messages,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            session=session,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            persona=persona,
        ))
        self._record_usage_and_logs(result)
        return result.final_content, result.tools_used, result.messages, result.stop_reason

    async def process_direct(
        self,
        content: str,
        *,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self.loop._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        return await self.loop._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )

    def _build_run_spec(
        self,
        *,
        initial_messages: list[dict[str, Any]],
        on_progress: Callable[..., Awaitable[None]] | None,
        on_stream: Callable[[str], Awaitable[None]] | None,
        on_stream_end: Callable[..., Awaitable[None]] | None,
        session: Session | None,
        channel: str,
        chat_id: str,
        message_id: str | None,
        persona: str | None,
    ) -> AgentRunSpec:
        """Build the AgentRunSpec used for one main-loop turn."""
        return AgentRunSpec(
            initial_messages=initial_messages,
            tools=self.loop.tools,
            model=self.loop.model,
            max_iterations=self.loop.max_iterations,
            max_tool_result_chars=self.loop.max_tool_result_chars,
            hook=self._build_hook(
                on_progress=on_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                channel=channel,
                chat_id=chat_id,
                message_id=message_id,
                persona=persona,
            ),
            error_message="Sorry, I encountered an error calling the AI model.",
            max_iterations_message=(
                f"I reached the maximum number of tool call iterations ({self.loop.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            ),
            concurrent_tools=True,
            workspace=self.loop.workspace,
            session_key=session.key if session else None,
            context_window_tokens=self.loop.context_window_tokens,
            context_block_limit=self.loop.context_block_limit,
            provider_retry_mode=self.loop.provider_retry_mode,
            progress_callback=on_progress,
            checkpoint_callback=self._checkpoint_callback(session),
        )

    def _build_hook(
        self,
        *,
        on_progress: Callable[..., Awaitable[None]] | None,
        on_stream: Callable[[str], Awaitable[None]] | None,
        on_stream_end: Callable[..., Awaitable[None]] | None,
        channel: str,
        chat_id: str,
        message_id: str | None,
        persona: str | None,
    ) -> AgentHook:
        """Build the core loop hook chain for one runner invocation."""
        loop_hook = LoopRunHook(
            prepare_request_messages=self.loop._prepare_request_messages,
            visible_response_text=self.loop._visible_response_text,
            strip_think=self.loop._strip_think,
            tool_hint=self.loop._tool_hint,
            set_tool_context=self.loop._set_tool_context,
            filter_persona_response=self.loop._filter_persona_response,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            persona=persona,
        )
        if self.loop._extra_hooks:
            return LoopHookChain(loop_hook, self.loop._extra_hooks)
        return loop_hook

    def _checkpoint_callback(self, session: Session | None):
        """Build the runner checkpoint callback for one invocation."""

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            self.loop._set_runtime_checkpoint(session, payload)

        return _checkpoint

    def _record_usage_and_logs(self, result) -> None:
        """Persist last usage counters and emit stable stop-reason logs."""
        self.loop._last_usage = result.usage
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.loop.max_iterations)
        elif result.stop_reason == "error":
            logger.error(
                "LLM returned error: {}",
                ((result.error or result.final_content) or "")[:200],
            )
