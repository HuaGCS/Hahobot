"""One-shot chat approval for pending shell commands."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from hahobot.agent.i18n import text
from hahobot.agent.tools.shell import ExecTool
from hahobot.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.session.manager import Session


class ExecApprovalCommandHandler:
    """Handle ``/approve`` without changing the configured confirmation mode."""

    _MAX_RESULT_CHARS = 4_000

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    @staticmethod
    def _response(msg: InboundMessage, content: str) -> OutboundMessage:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata={"render_as": "text"},
        )

    async def handle(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        language = self.loop._get_session_language(session)
        parts = msg.content.strip().split()
        if parts == ["/approve"]:
            all_pending = False
        elif len(parts) == 2 and parts[0] == "/approve" and parts[1].casefold() == "all":
            all_pending = True
        else:
            return self._response(msg, text(language, "approve_usage"))

        # Consumption is atomic and happens before the first await: concurrent or
        # repeated approvals can never execute the same pending request twice.
        requests = self.loop.exec_approval_store.consume(
            session_key=session.key,
            sender_id=msg.sender_id,
            channel=msg.channel,
            chat_id=msg.chat_id,
            all_pending=all_pending,
        )
        if not requests:
            return self._response(msg, text(language, "approve_no_pending"))

        tool = self.loop.tools.get("exec")
        rendered: list[str] = []
        for request in requests:
            if isinstance(tool, ExecTool):
                result = await tool.execute_approved(request)
                failed = self._failed(result)
            else:
                result = text(language, "approve_exec_unavailable")
                failed = True
            result = self._truncate(result)
            key = "approve_result_failed" if failed else "approve_result_success"
            rendered.append(
                text(
                    language,
                    key,
                    command=request.approval_preview,
                    result=result,
                )
            )

        header = text(language, "approve_results_header", count=len(rendered))
        return self._response(msg, "\n\n".join((header, *rendered)))

    @classmethod
    def _truncate(cls, result: str) -> str:
        if len(result) <= cls._MAX_RESULT_CHARS:
            return result
        return result[: cls._MAX_RESULT_CHARS - 3] + "..."

    @staticmethod
    def _failed(result: str) -> bool:
        match = re.search(r"(?:^|\n)Exit code:\s*(-?\d+)\s*$", result)
        if match is not None:
            return int(match.group(1)) != 0
        return result.lstrip().casefold().startswith("error")
