"""Per-invocation statistics for the interactive agent CLI."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field


def _usage_value(usage: Mapping[str, int], key: str) -> int:
    try:
        return max(0, int(usage.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{seconds:.1f}s"


@dataclass
class CliSessionStats:
    """Collect stream metrics and format one interactive CLI exit summary."""

    started_at: float = field(default_factory=time.monotonic)
    estimated_stream_tokens: int = 0
    active_stream_seconds: float = 0.0
    streamed_turns: int = 0
    usage_totals: dict[str, int] = field(default_factory=dict)
    model_calls: int = 0

    def record_usage(self, usage: Mapping[str, int]) -> None:
        """Accumulate one provider attempt, normalizing its total before addition."""
        prompt_tokens = _usage_value(usage, "prompt_tokens") or _usage_value(usage, "input_tokens")
        completion_tokens = _usage_value(usage, "completion_tokens") or _usage_value(
            usage, "output_tokens"
        )
        total_tokens = _usage_value(usage, "total_tokens") or (prompt_tokens + completion_tokens)
        cached_tokens = _usage_value(usage, "cached_tokens")
        normalized = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
        }
        for key, value in normalized.items():
            self.usage_totals[key] = self.usage_totals.get(key, 0) + value
        self.model_calls += 1

    def record_stream(self, estimated_tokens: int, active_seconds: float) -> None:
        """Add one completed streamed reply to the weighted rate totals."""
        tokens = max(0, int(estimated_tokens))
        seconds = max(0.0, float(active_seconds))
        if tokens <= 0 or seconds <= 0:
            return
        self.estimated_stream_tokens += tokens
        self.active_stream_seconds += seconds
        self.streamed_turns += 1

    def summary_lines(
        self,
        *,
        usage: Mapping[str, int] | None = None,
        turn_count: int,
        model_call_count: int | None = None,
        now: float | None = None,
    ) -> list[str]:
        """Return plain-text summary lines suitable for Rich or prompt_toolkit output."""
        use_observed_usage = usage is None
        usage = self.usage_totals if usage is None else usage
        if model_call_count is None and use_observed_usage:
            model_call_count = self.model_calls
        prompt_tokens = _usage_value(usage, "prompt_tokens")
        completion_tokens = _usage_value(usage, "completion_tokens")
        total_tokens = _usage_value(usage, "total_tokens") or (prompt_tokens + completion_tokens)
        cached_tokens = _usage_value(usage, "cached_tokens")
        elapsed = max(0.0, (time.monotonic() if now is None else now) - self.started_at)

        turn_line = f"💬 Completed agent turns: {max(0, int(turn_count))}"
        if model_call_count is not None:
            turn_line += f" · Model calls: {max(0, int(model_call_count))}"
        lines = [turn_line]
        if total_tokens > 0 or cached_tokens > 0:
            token_line = (
                f"📊 Tokens: {prompt_tokens:,} in / {completion_tokens:,} out / "
                f"{total_tokens:,} total"
            )
            if cached_tokens > 0:
                token_line += f" ({cached_tokens:,} cached)"
            lines.append(token_line)
        else:
            lines.append("📊 Tokens: provider usage unavailable")

        if self.estimated_stream_tokens > 0 and self.active_stream_seconds > 0:
            average_rate = self.estimated_stream_tokens / self.active_stream_seconds
            lines.append(
                "⚡ Stream: "
                f"≈{self.estimated_stream_tokens:,} tok / "
                f"{self.active_stream_seconds:.1f}s active · "
                f"avg ≈{average_rate:.1f} tok/s"
            )
        else:
            lines.append("⚡ Stream: no completed streamed output")
        lines.append(f"⏱ Duration: {_format_duration(elapsed)}")
        return lines
