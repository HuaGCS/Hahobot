"""Streaming renderer for CLI output.

Uses Rich Live with auto_refresh=False for stable, flicker-free
markdown rendering during streaming. Ellipsis mode handles overflow.
"""

from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from hahobot import __logo__
from hahobot.utils.helpers import estimate_text_tokens


def _make_console() -> Console:
    # Only force ANSI (colors + cursor/erase codes for the live spinner) when
    # stdout is a real TTY. When output is piped/redirected/captured, pass
    # force_terminal=None so Rich auto-detects and emits no escape codes —
    # otherwise the spinner's redraw frames leak as literal `\x1b[2K` junk.
    force = True if sys.stdout.isatty() else None
    return Console(file=sys.stdout, force_terminal=force)


class ThinkingSpinner:
    """Spinner that shows 'hahobot is thinking...' with pause support."""

    def __init__(self, console: Console | None = None):
        c = console or _make_console()
        self._spinner = c.status("[dim]hahobot is thinking...[/dim]", spinner="dots")
        self._active = False

    def __enter__(self):
        self._spinner.start()
        self._active = True
        return self

    def __exit__(self, *exc):
        self._active = False
        self._spinner.stop()
        return False

    def pause(self):
        """Context manager: temporarily stop spinner for clean output."""
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            if self._spinner and self._active:
                self._spinner.stop()
            try:
                yield
            finally:
                if self._spinner and self._active:
                    self._spinner.start()

        return _ctx()


class StreamRenderer:
    """Rich Live streaming with markdown. auto_refresh=False avoids render races.

    Deltas arrive pre-filtered (no <think> tags) from the agent loop.

    Flow per round:
      spinner -> first visible delta -> header + Live renders ->
      on_end -> Live stops (content stays on screen)
    """

    def __init__(
        self,
        render_markdown: bool = True,
        show_spinner: bool = True,
        interactive: bool = False,
    ):
        self._md = render_markdown
        self._show_spinner = show_spinner
        self._interactive = interactive
        self._buf = ""
        self._all_text = ""
        self._live: Live | None = None
        self._t = 0.0
        self._segment_started_at: float | None = None
        self._stream_elapsed = 0.0
        self._rate_printed = False
        self.streamed = False
        self._spinner: ThinkingSpinner | None = None
        if not self._interactive:
            self._start_spinner()

    def _render(self):
        return Markdown(self._buf) if self._md and self._buf else Text(self._buf or "")

    def _start_spinner(self) -> None:
        if self._show_spinner:
            self._spinner = ThinkingSpinner()
            self._spinner.__enter__()

    def _stop_spinner(self) -> None:
        if self._spinner:
            self._spinner.__exit__(None, None, None)
            self._spinner = None

    async def on_delta(self, delta: str) -> None:
        now = time.monotonic()
        if self._segment_started_at is None:
            self._segment_started_at = now
        self.streamed = True
        self._buf += delta
        self._all_text += delta
        if self._interactive:
            return
        if self._live is None:
            if not self._buf.strip():
                return
            self._stop_spinner()
            c = _make_console()
            c.print()
            c.print(f"[cyan]{__logo__} hahobot[/cyan]")
            self._live = Live(self._render(), console=c, auto_refresh=False)
            self._live.start()
        if "\n" in delta or (now - self._t) > 0.05:
            self._live.update(self._render())
            self._live.refresh()
            self._t = now

    async def on_end(self, *, resuming: bool = False) -> None:
        self._finish_segment()
        if self._interactive:
            if resuming:
                self._buf = ""
            elif self._buf:
                from hahobot.cli.commands import interactive

                await interactive._print_interactive_response(
                    self._buf,
                    render_markdown=self._md,
                )
                if rate_text := self._take_rate_text():
                    await interactive._print_interactive_line(rate_text)
            return
        if self._live:
            self._live.update(self._render())
            self._live.refresh()
            self._live.stop()
            self._live = None
        self._stop_spinner()
        if resuming:
            self._buf = ""
            self._start_spinner()
        else:
            c = _make_console()
            if rate_text := self._take_rate_text():
                c.print(f"[dim]{rate_text}[/dim]")
            c.print()

    def _finish_segment(self) -> None:
        """Accumulate active streamed-generation time for this turn."""
        if self._segment_started_at is None:
            return
        self._stream_elapsed += max(0.0, time.monotonic() - self._segment_started_at)
        self._segment_started_at = None

    def _take_rate_text(self) -> str | None:
        """Return the one-shot visible-output throughput footer."""
        if self._rate_printed:
            return None
        token_count, stream_elapsed = self.rate_metrics()
        if token_count <= 0 or stream_elapsed <= 0:
            return None
        self._rate_printed = True
        rate = token_count / stream_elapsed
        return f"⚡ ≈{rate:.1f} tok/s · ≈{token_count} tok · {stream_elapsed:.1f}s"

    def rate_metrics(self) -> tuple[int, float]:
        """Return estimated streamed text tokens and active generation seconds."""
        elapsed = self._stream_elapsed
        if self._segment_started_at is not None:
            elapsed += max(0.0, time.monotonic() - self._segment_started_at)
        return estimate_text_tokens(self._all_text), elapsed

    def stop_for_input(self) -> None:
        """Stop spinner before user input to avoid prompt_toolkit conflicts."""
        self._stop_spinner()

    async def close(self) -> None:
        """Stop spinner/live without rendering a final streamed round."""
        if self._live:
            self._live.stop()
            self._live = None
        self._stop_spinner()
