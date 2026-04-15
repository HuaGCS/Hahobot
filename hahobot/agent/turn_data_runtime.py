"""Turn data preparation and persistence helpers for AgentLoop."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from hahobot.agent.context import ContextBuilder
from hahobot.utils.helpers import image_placeholder_text
from hahobot.utils.helpers import (
    truncate_text as truncate_text_value,
)

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop, _PreparedTurnContext, _SessionTurnState
    from hahobot.bus.events import InboundMessage
    from hahobot.session.manager import Session


class TurnDataRuntimeManager:
    """Own per-turn message preparation, prompt compaction, and session persistence."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    async def prepare_turn_context(
        self,
        msg: InboundMessage,
        state: _SessionTurnState,
        *,
        history: list[dict[str, Any]] | None,
    ) -> _PreparedTurnContext:
        """Warm runtime services and resolve per-turn history/memory context."""
        await self.loop._connect_mcp()
        await self.loop._run_preflight_token_consolidation(state.session)
        self.loop._set_tool_context(
            state.channel,
            state.chat_id,
            msg.metadata.get("message_id"),
            persona=state.persona,
        )
        turn_history = history if history is not None else state.session.get_history(max_messages=0)
        memorix_context = await self.loop._maybe_start_memorix_session(state.session)
        memory_scope = self.loop._memory_scope(
            state.session,
            channel=state.channel,
            chat_id=state.chat_id,
            sender_id=msg.sender_id,
            persona=state.persona,
            language=state.language,
            query=msg.content,
        )
        resolved_memory = await self.loop.memory_router.prepare_context(memory_scope)
        return self.loop._prepared_turn_context_type()(
            state=state,
            history=turn_history,
            memory_scope=memory_scope,
            memory_context=resolved_memory.block,
            memorix_context=memorix_context,
        )

    def build_turn_messages(
        self,
        msg: InboundMessage,
        turn: _PreparedTurnContext,
        *,
        current_message: str | None = None,
        current_role: str = "user",
        media: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the request messages for the current turn."""
        messages = self.loop.context.build_messages(
            history=turn.history,
            current_message=current_message or msg.content,
            skill_names=self.loop._runtime_skill_names(),
            media=media,
            channel=turn.state.channel,
            chat_id=turn.state.chat_id,
            persona=turn.state.persona,
            language=turn.state.language,
            current_role=current_role,
            session_summary=turn.state.pending_summary,
            memory_context=turn.memory_context,
        )
        self.loop._append_untrusted_system_section(
            messages,
            "Workspace Memory (Memorix)",
            turn.memorix_context,
        )
        return messages

    def sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if (
                block.get("type") == "image_url"
                and block.get("image_url", {}).get("url", "").startswith("data:image/")
            ):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if truncate_text and len(text) > self.loop.max_tool_result_chars:
                    text = truncate_text_value(text, self.loop.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def prompt_budget_tokens(self) -> int:
        """Return the current-turn prompt budget used for tool-result compaction."""
        return max(0, int(self.loop.context_window_tokens))

    def truncate_prompt_text(self, text: str, max_chars: int) -> str:
        """Trim text for in-flight prompt compaction."""
        if max_chars <= 0:
            return self.loop._CONTEXT_TOOL_RESULT_OMIT
        if len(text) <= max_chars:
            return text
        if max_chars <= len(self.loop._CONTEXT_TOOL_RESULT_SUFFIX):
            return text[:max_chars]
        return (
            text[: max_chars - len(self.loop._CONTEXT_TOOL_RESULT_SUFFIX)]
            + self.loop._CONTEXT_TOOL_RESULT_SUFFIX
        )

    def compact_tool_result_for_prompt(self, content: Any, max_chars: int) -> Any:
        """Compact a tool result just enough to keep the current turn within budget."""
        if max_chars <= 0:
            return self.loop._CONTEXT_TOOL_RESULT_OMIT

        if isinstance(content, str):
            return self.truncate_prompt_text(content, max_chars)

        if isinstance(content, list):
            remaining = max_chars
            compacted: list[dict[str, Any]] = []
            for block in self.sanitize_persisted_blocks(content):
                if remaining <= 0:
                    break
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                ):
                    text = block["text"]
                    trimmed = self.truncate_prompt_text(text, remaining)
                    compacted.append({**block, "text": trimmed})
                    remaining -= len(trimmed)
                    if trimmed != text:
                        break
                    continue

                raw = json.dumps(block, ensure_ascii=False)
                trimmed = self.truncate_prompt_text(raw, remaining)
                compacted.append({"type": "text", "text": trimmed})
                remaining -= len(trimmed)
                if trimmed != raw:
                    break

            return compacted or [{"type": "text", "text": self.loop._CONTEXT_TOOL_RESULT_OMIT}]

        if content is None:
            return None
        return self.truncate_prompt_text(json.dumps(content, ensure_ascii=False), max_chars)

    def apply_prompt_compaction_step(
        self,
        prepared: list[dict[str, Any]],
        indices: list[int],
        max_chars: int,
    ) -> int:
        """Compact one group of tool results and return the approximate token savings."""
        saved_tokens = 0
        for idx in indices:
            current = prepared[idx]
            compacted = self.compact_tool_result_for_prompt(current.get("content"), max_chars)
            if compacted == current.get("content"):
                continue
            compacted_message = {**current, "content": compacted}
            saved_tokens += max(
                0,
                self.loop._estimate_message_tokens(current)
                - self.loop._estimate_message_tokens(compacted_message),
            )
            prepared[idx] = compacted_message
        return saved_tokens

    def prepare_request_messages(
        self,
        messages: list[dict[str, Any]],
        tool_defs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Shrink older tool results on demand so system prompt and recent context still fit."""
        budget = self.prompt_budget_tokens()
        if budget <= 0:
            return messages

        estimated, source = self.loop._estimate_prompt_tokens_chain(messages, tool_defs)
        if estimated <= budget:
            return messages

        tool_indices = [idx for idx, message in enumerate(messages) if message.get("role") == "tool"]
        if not tool_indices:
            logger.warning(
                "Prompt over budget for current turn: {}/{} via {} (no tool results to compact)",
                estimated,
                self.loop.context_window_tokens,
                source,
            )
            return messages

        prepared = list(messages)
        older_indices = tool_indices[:-1] if len(tool_indices) > 1 else tool_indices
        newest_indices = tool_indices[-1:] if len(tool_indices) > 1 else []
        approx_estimated = estimated

        for indices in (older_indices, newest_indices):
            for max_chars in self.loop._CONTEXT_TOOL_RESULT_CHAR_STEPS:
                saved_tokens = self.apply_prompt_compaction_step(prepared, indices, max_chars)
                if saved_tokens <= 0:
                    continue
                approx_estimated = max(0, approx_estimated - saved_tokens)
                if (
                    approx_estimated > budget
                    and max_chars != self.loop._CONTEXT_TOOL_RESULT_CHAR_STEPS[-1]
                ):
                    continue
                estimated, source = self.loop._estimate_prompt_tokens_chain(prepared, tool_defs)
                approx_estimated = estimated
                if estimated <= budget:
                    logger.info(
                        "Compacted tool results for current turn: {}/{} via {}",
                        estimated,
                        self.loop.context_window_tokens,
                        source,
                    )
                    return prepared

        logger.warning(
            "Prompt still over budget after tool-result compaction: {}/{} via {}",
            estimated,
            self.loop.context_window_tokens,
            source,
        )
        return prepared

    def save_turn(
        self,
        session: Session,
        messages: list[dict[str, Any]],
        skip: int,
    ) -> list[dict[str, Any]]:
        """Save new-turn messages into session, truncating large tool results."""
        persisted: list[dict[str, Any]] = []
        for message in messages[skip:]:
            entry = dict(message)
            role = entry.get("role")
            content = entry.get("content")

            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue
            if role == "tool":
                if isinstance(content, str) and len(content) > self.loop.max_tool_result_chars:
                    entry["content"] = truncate_text_value(content, self.loop.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self.sanitize_persisted_blocks(content, truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = self.sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered

            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
            persisted.append(entry)

        session.updated_at = datetime.now()
        return persisted
