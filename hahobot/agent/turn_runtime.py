"""Inbound turn orchestration helpers for AgentLoop."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Awaitable, Callable

from loguru import logger

from hahobot.agent.skills import SkillsLoader
from hahobot.agent.tools.message import MessageTool
from hahobot.bus.events import OutboundMessage
from hahobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.events import InboundMessage


class TurnRuntimeManager:
    """Own the high-level message-processing flow for one AgentLoop."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    async def process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process one inbound message and return the outbound payload."""
        await self.loop._reload_runtime_config_if_needed()
        if msg.channel != "system":
            msg = self.loop._normalize_session_message(msg)

        if msg.channel == "system":
            return await self._process_system_message(msg)
        return await self._process_chat_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )

    @staticmethod
    def _system_origin(chat_id: str) -> tuple[str, str]:
        """Parse system-message origin from `channel:chat_id`."""
        return msg_parts if len(msg_parts := chat_id.split(":", 1)) == 2 else ("cli", chat_id)

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """Process one background/system message routed back into a chat session."""
        channel, chat_id = self._system_origin(msg.chat_id)
        logger.info("Processing system message from {}", msg.sender_id)
        state = self.loop._load_session_turn_state(
            key=f"{channel}:{chat_id}",
            channel=channel,
            chat_id=chat_id,
        )
        if msg.sender_id == "subagent" and self._persist_subagent_followup(state.session, msg):
            self.loop.sessions.save(state.session)
        turn = await self.loop._prepare_turn_context(msg, state, history=None)
        current_role = "assistant" if msg.sender_id == "subagent" else "user"
        messages = self.loop._build_turn_messages(
            msg,
            turn,
            current_message=msg.content,
            current_role=current_role,
            omit_current_message=msg.sender_id == "subagent",
        )
        final_content, _, all_msgs, stop_reason = await self.loop._run_agent_loop(
            messages,
            session=turn.state.session,
            channel=turn.state.channel,
            chat_id=turn.state.chat_id,
            message_id=msg.metadata.get("message_id"),
            persona=turn.state.persona,
        )
        persisted_messages = self.loop._save_turn(turn.state.session, all_msgs, 1 + len(turn.history))
        self._record_turn_skill_usage(all_msgs, stop_reason)
        self.loop.sessions.save(turn.state.session)
        await self.loop._commit_memory_turn(
            scope=turn.memory_scope,
            inbound_content=None if msg.sender_id == "subagent" else msg.content,
            outbound_content=final_content,
            persisted_messages=persisted_messages,
        )
        self.loop._ensure_background_token_consolidation(turn.state.session)
        return await self.loop._maybe_attach_voice_reply(
            OutboundMessage(
                channel=turn.state.channel,
                chat_id=turn.state.chat_id,
                content=final_content or "Background task completed.",
            ),
            persona=turn.state.persona,
        )

    async def _process_chat_message(
        self,
        msg: InboundMessage,
        *,
        session_key: str | None,
        on_progress: Callable[[str], Awaitable[None]] | None,
        on_stream: Callable[[str], Awaitable[None]] | None,
        on_stream_end: Callable[..., Awaitable[None]] | None,
    ) -> OutboundMessage | None:
        """Process one normal inbound chat message."""
        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        turn_state = self.loop._load_session_turn_state(
            key=session_key or msg.session_key,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )

        slash_response = await self.loop._command_router.dispatch(
            self.loop._command_context(msg, session=turn_state.session, key=turn_state.key)
        )
        if slash_response is not None:
            return slash_response

        self._start_message_tool_turn()
        history = turn_state.session.get_history(max_messages=0, include_timestamps=True)
        user_persisted_early = self._persist_user_message_early(turn_state.session, msg)
        turn = await self.loop._prepare_turn_context(msg, turn_state, history=history)
        initial_messages = self.loop._build_turn_messages(
            msg,
            turn,
            media=msg.media if msg.media else None,
        )

        final_content, _, all_msgs, stop_reason = await self.loop._run_agent_loop(
            initial_messages,
            on_progress=on_progress or self._bus_progress_callback(msg),
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            session=turn.state.session,
            channel=turn.state.channel,
            chat_id=turn.state.chat_id,
            message_id=msg.metadata.get("message_id"),
            persona=turn.state.persona,
        )

        final_content = self._normalized_final_content(final_content)
        persisted_messages = self.loop._save_turn(
            turn.state.session,
            all_msgs,
            1 + len(turn.history) + (1 if user_persisted_early else 0),
        )
        self._record_turn_skill_usage(all_msgs, stop_reason)
        self.loop._clear_pending_user_turn(turn.state.session)
        self.loop.sessions.save(turn.state.session)
        await self.loop._commit_memory_turn(
            scope=turn.memory_scope,
            inbound_content=msg.content,
            outbound_content=final_content,
            persisted_messages=persisted_messages,
        )
        self.loop._ensure_background_token_consolidation(turn.state.session)

        if self._message_tool_sent_in_turn():
            return None
        return await self._build_chat_outbound(
            msg,
            final_content=final_content,
            stop_reason=stop_reason,
            on_stream=on_stream,
            channel=turn.state.channel,
            chat_id=turn.state.chat_id,
            persona=turn.state.persona,
        )

    def _start_message_tool_turn(self) -> None:
        """Reset per-turn message-tool state when available."""
        if message_tool := self.loop.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

    def _message_tool_sent_in_turn(self) -> bool:
        """Return whether the message tool already delivered the final reply this turn."""
        if mt := self.loop.tools.get("message"):
            return isinstance(mt, MessageTool) and mt._sent_in_turn
        return False

    def _persist_user_message_early(self, session, msg: InboundMessage) -> bool:
        """Persist a plain user message before the model turn starts."""
        if not isinstance(msg.content, str) or not msg.content.strip() or msg.media:
            return False

        from datetime import datetime

        session.messages.append({
            "role": "user",
            "content": msg.content,
            "timestamp": datetime.now().isoformat(),
        })
        self.loop._mark_pending_user_turn(session)
        self.loop.sessions.save(session)
        return True

    @staticmethod
    def _persist_subagent_followup(session, msg: InboundMessage) -> bool:
        """Persist a subagent follow-up before prompt assembly so it survives retries/crashes."""
        metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
        task_id = metadata.get("subagent_task_id")
        if task_id and any(
            message.get("injected_event") == "subagent_result"
            and message.get("subagent_task_id") == task_id
            for message in session.messages
        ):
            return False

        session.add_message(
            "assistant",
            msg.content,
            sender_id=msg.sender_id,
            injected_event=str(metadata.get("injected_event") or "subagent_result"),
            subagent_task_id=task_id,
            subagent_status=metadata.get("subagent_status"),
            subagent_label=metadata.get("subagent_label"),
        )
        return True

    def _bus_progress_callback(
        self,
        msg: InboundMessage,
    ) -> Callable[[str], Awaitable[None]]:
        """Create the default bus-backed progress callback for one turn."""

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.loop.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
                metadata=meta,
            ))

        return _bus_progress

    @staticmethod
    def _normalized_final_content(final_content: str | None) -> str:
        """Guarantee a non-empty final text payload."""
        if final_content is None or not final_content.strip():
            return EMPTY_FINAL_RESPONSE_MESSAGE
        return final_content

    @staticmethod
    def _decode_tool_call_arguments(tool_call: dict) -> dict[str, str]:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return {}
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            return arguments
        if not isinstance(arguments, str):
            return {}
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _record_turn_skill_usage(
        self,
        messages: list[dict],
        stop_reason: str,
    ) -> None:
        """Update workspace skill usage stats from successful `read_file` tool calls."""
        try:
            loader = SkillsLoader(self.loop.workspace)
            tool_results = {
                str(msg.get("tool_call_id")): msg.get("content")
                for msg in messages
                if msg.get("role") == "tool" and msg.get("tool_call_id")
            }
            used_names: list[str] = []
            for msg in messages:
                if msg.get("role") != "assistant":
                    continue
                for tool_call in msg.get("tool_calls") or []:
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function")
                    if not isinstance(function, dict) or function.get("name") != "read_file":
                        continue
                    result = tool_results.get(str(tool_call.get("id")))
                    if isinstance(result, str) and result.startswith("Error"):
                        continue
                    path = self._decode_tool_call_arguments(tool_call).get("path")
                    if not isinstance(path, str) or not path.strip():
                        continue
                    if skill_name := loader.workspace_skill_name_for_path(path):
                        used_names.append(skill_name)

            if not used_names:
                return

            loader.record_skill_usage_batch(
                used_names,
                used_on=datetime.now(tz=UTC).date().isoformat(),
                success=stop_reason not in {"error", "tool_error", "empty_final_response", "max_iterations"},
            )
        except Exception:
            logger.exception("Skill usage writeback failed")

    async def _build_chat_outbound(
        self,
        msg: InboundMessage,
        *,
        final_content: str,
        stop_reason: str,
        on_stream: Callable[[str], Awaitable[None]] | None,
        channel: str,
        chat_id: str,
        persona: str | None,
    ) -> OutboundMessage | None:
        """Convert one completed chat turn into the final outbound payload."""
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        outbound = await self.loop._maybe_attach_voice_reply(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=final_content,
                metadata=msg.metadata or {},
            ),
            persona=persona,
        )
        if outbound is None:
            return None

        meta = dict(outbound.metadata or {})
        content = outbound.content
        if on_stream is not None:
            if outbound.media:
                content = ""
            elif stop_reason != "error":
                meta["_streamed"] = True
        return OutboundMessage(
            channel=outbound.channel,
            chat_id=outbound.chat_id,
            content=content,
            reply_to=outbound.reply_to,
            media=list(outbound.media or []),
            metadata=meta,
        )
