"""Helpers for manually triggering session token consolidation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hahobot.session.manager import Session


@dataclass(frozen=True)
class SessionCompactReport:
    """Serializable result for one manual session compaction run."""

    key: str
    message_count: int
    live_message_count: int
    archived_message_count: int
    last_consolidated_before: int
    last_consolidated_after: int
    prompt_tokens_before: int | None
    prompt_tokens_after: int | None
    estimate_source_before: str | None
    estimate_source_after: str | None
    budget_tokens: int | None
    target_tokens: int | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "message_count": self.message_count,
            "live_message_count": self.live_message_count,
            "archived_message_count": self.archived_message_count,
            "last_consolidated_before": self.last_consolidated_before,
            "last_consolidated_after": self.last_consolidated_after,
            "prompt_tokens_before": self.prompt_tokens_before,
            "prompt_tokens_after": self.prompt_tokens_after,
            "estimate_source_before": self.estimate_source_before,
            "estimate_source_after": self.estimate_source_after,
            "budget_tokens": self.budget_tokens,
            "target_tokens": self.target_tokens,
            "status": self.status,
        }


def _status_message(status: str) -> str:
    return {
        "compacted": "Compaction completed.",
        "within_budget": "Session is already within the automatic compaction budget.",
        "already_compacted": "No unconsolidated session messages remain.",
        "empty": "Session has no messages to compact.",
        "disabled": "Compaction is unavailable because contextWindowTokens is disabled.",
        "no_safe_boundary": "Session is over budget, but no safe user-turn boundary was found.",
    }.get(status, status)


def render_session_compact_text(report: SessionCompactReport) -> str:
    """Render one manual compaction report as plain text."""
    lines = [
        "hahobot sessions compact",
        f"Session: {report.key}",
        f"Messages: {report.message_count} total, {report.live_message_count} live, "
        f"{report.archived_message_count} archived",
        f"Cursor: {report.last_consolidated_before} -> {report.last_consolidated_after}",
    ]
    if report.prompt_tokens_before is not None and report.prompt_tokens_after is not None:
        lines.append(
            f"Prompt estimate: {report.prompt_tokens_before} -> {report.prompt_tokens_after} tokens"
        )
    if report.estimate_source_before:
        if report.estimate_source_before == report.estimate_source_after:
            lines.append(f"Estimate source: {report.estimate_source_before}")
        else:
            lines.append(
                "Estimate source: "
                f"{report.estimate_source_before or 'n/a'} -> {report.estimate_source_after or 'n/a'}"
            )
    if report.budget_tokens is not None and report.target_tokens is not None:
        lines.append(
            f"Budget: {report.budget_tokens} tokens (target <= {report.target_tokens})"
        )
    lines.append(f"Result: {_status_message(report.status)}")
    return "\n".join(lines)


async def compact_session(session: Session, loop: Any) -> SessionCompactReport:
    """Run the existing token-budget consolidator for one session and summarize the result."""
    consolidator = loop.memory_consolidator
    before_cursor = session.last_consolidated
    message_count = len(session.messages)
    live_before = max(0, message_count - before_cursor)

    prompt_tokens_before: int | None = None
    prompt_tokens_after: int | None = None
    estimate_source_before: str | None = None
    estimate_source_after: str | None = None

    budget_tokens = consolidator.context_window_tokens - consolidator.max_completion_tokens
    budget_tokens -= consolidator._SAFETY_BUFFER
    target_tokens = budget_tokens // 2 if budget_tokens > 0 else None
    if consolidator.context_window_tokens > 0:
        prompt_tokens_before, estimate_source_before = consolidator.estimate_session_prompt_tokens(
            session
        )

    if message_count <= 0:
        return SessionCompactReport(
            key=session.key,
            message_count=message_count,
            live_message_count=live_before,
            archived_message_count=0,
            last_consolidated_before=before_cursor,
            last_consolidated_after=before_cursor,
            prompt_tokens_before=prompt_tokens_before,
            prompt_tokens_after=prompt_tokens_before,
            estimate_source_before=estimate_source_before,
            estimate_source_after=estimate_source_before,
            budget_tokens=budget_tokens if budget_tokens > 0 else None,
            target_tokens=target_tokens,
            status="empty",
        )

    if live_before <= 0:
        return SessionCompactReport(
            key=session.key,
            message_count=message_count,
            live_message_count=0,
            archived_message_count=0,
            last_consolidated_before=before_cursor,
            last_consolidated_after=before_cursor,
            prompt_tokens_before=prompt_tokens_before,
            prompt_tokens_after=prompt_tokens_before,
            estimate_source_before=estimate_source_before,
            estimate_source_after=estimate_source_before,
            budget_tokens=budget_tokens if budget_tokens > 0 else None,
            target_tokens=target_tokens,
            status="already_compacted",
        )

    if budget_tokens <= 0:
        return SessionCompactReport(
            key=session.key,
            message_count=message_count,
            live_message_count=live_before,
            archived_message_count=0,
            last_consolidated_before=before_cursor,
            last_consolidated_after=before_cursor,
            prompt_tokens_before=prompt_tokens_before,
            prompt_tokens_after=prompt_tokens_before,
            estimate_source_before=estimate_source_before,
            estimate_source_after=estimate_source_before,
            budget_tokens=None,
            target_tokens=None,
            status="disabled",
        )

    await consolidator.maybe_consolidate_by_tokens(session)

    after_cursor = session.last_consolidated
    live_after = max(0, message_count - after_cursor)
    if consolidator.context_window_tokens > 0:
        prompt_tokens_after, estimate_source_after = consolidator.estimate_session_prompt_tokens(
            session
        )

    if after_cursor > before_cursor:
        status = "compacted"
    elif prompt_tokens_before is not None and prompt_tokens_before > budget_tokens:
        status = "no_safe_boundary"
    else:
        status = "within_budget"

    return SessionCompactReport(
        key=session.key,
        message_count=message_count,
        live_message_count=live_after,
        archived_message_count=max(0, after_cursor - before_cursor),
        last_consolidated_before=before_cursor,
        last_consolidated_after=after_cursor,
        prompt_tokens_before=prompt_tokens_before,
        prompt_tokens_after=prompt_tokens_after,
        estimate_source_before=estimate_source_before,
        estimate_source_after=estimate_source_after,
        budget_tokens=budget_tokens,
        target_tokens=target_tokens,
        status=status,
    )
