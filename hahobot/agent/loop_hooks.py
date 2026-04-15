"""Hook implementations used by the agent loop runtime."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from loguru import logger

from hahobot.agent.hook import AgentHook, AgentHookContext, CompositeHook


class LoopHookChain(AgentHook):
    """Run the core loop hook before any extra hooks."""

    __slots__ = ("_primary", "_extras")

    def __init__(self, primary: AgentHook, extra_hooks: list[AgentHook]) -> None:
        super().__init__(reraise=True)
        self._primary = primary
        self._extras = CompositeHook(extra_hooks)

    def wants_streaming(self) -> bool:
        return self._primary.wants_streaming() or self._extras.wants_streaming()

    def prepare_messages(
        self,
        context: AgentHookContext,
        tool_definitions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self._primary.prepare_messages(context, tool_definitions)

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._primary.before_iteration(context)
        await self._extras.before_iteration(context)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        await self._primary.on_stream(context, delta)
        await self._extras.on_stream(context, delta)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        await self._primary.on_stream_end(context, resuming=resuming)
        await self._extras.on_stream_end(context, resuming=resuming)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        await self._primary.before_execute_tools(context)
        await self._extras.before_execute_tools(context)

    async def after_iteration(self, context: AgentHookContext) -> None:
        await self._primary.after_iteration(context)
        await self._extras.after_iteration(context)

    def normalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return self._primary.normalize_content(context, content)

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        content = self._primary.finalize_content(context, content)
        return self._extras.finalize_content(context, content)


class LoopRunHook(AgentHook):
    """Bridge agent runner lifecycle callbacks back into AgentLoop behavior."""

    __slots__ = (
        "_channel",
        "_chat_id",
        "_filter_persona_response",
        "_message_id",
        "_on_progress",
        "_on_stream",
        "_on_stream_end",
        "_persona",
        "_prepare_request_messages",
        "_set_tool_context",
        "_stream_buf",
        "_strip_think",
        "_tool_hint",
        "_visible_response_text",
    )

    def __init__(
        self,
        *,
        prepare_request_messages: Callable[
            [list[dict[str, Any]], list[dict[str, Any]]],
            list[dict[str, Any]],
        ],
        visible_response_text: Callable[[str | None, str | None], str],
        strip_think: Callable[[str | None], str | None],
        tool_hint: Callable[[list], str],
        set_tool_context: Callable[[str, str, str | None, str | None], None],
        filter_persona_response: Callable[[str | None, str | None], str | None],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        persona: str | None = None,
    ) -> None:
        super().__init__(reraise=True)
        self._prepare_request_messages = prepare_request_messages
        self._visible_response_text = visible_response_text
        self._strip_think = strip_think
        self._tool_hint = tool_hint
        self._set_tool_context = set_tool_context
        self._filter_persona_response = filter_persona_response
        self._on_progress = on_progress
        self._on_stream = on_stream
        self._on_stream_end = on_stream_end
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._persona = persona
        self._stream_buf = ""

    def wants_streaming(self) -> bool:
        return self._on_stream is not None

    def prepare_messages(
        self,
        context: AgentHookContext,
        tool_definitions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self._prepare_request_messages(context.messages, tool_definitions)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        prev_clean = self._visible_response_text(self._stream_buf, self._persona)
        self._stream_buf += delta
        new_clean = self._visible_response_text(self._stream_buf, self._persona)
        incremental = new_clean[len(prev_clean):]
        if incremental and self._on_stream:
            await self._on_stream(incremental)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        if self._on_stream_end:
            await self._on_stream_end(resuming=resuming)
        self._stream_buf = ""

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if self._on_progress:
            if not self._on_stream:
                thought = self._visible_response_text(
                    context.response.content if context.response else None,
                    self._persona,
                )
                if thought:
                    await self._on_progress(thought)
            tool_hint = self._strip_think(self._tool_hint(context.tool_calls))
            await self._on_progress(tool_hint, tool_hint=True)
        for tc in context.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            logger.info("Tool call: {}({})", tc.name, args_str[:200])
        self._set_tool_context(self._channel, self._chat_id, self._message_id, self._persona)

    def normalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return self._strip_think(content)

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        visible = self._filter_persona_response(content, self._persona)
        return visible or content
