from __future__ import annotations

import io
import re
from contextlib import redirect_stdout

from prompt_toolkit.formatted_text import ANSI

from hahobot.cli.commands import interactive
from hahobot.cli.stream import StreamRenderer


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


async def test_interactive_stream_renderer_prints_once_without_live_escape_codes(monkeypatch):
    async def fake_run_in_terminal(write):
        write()

    def fake_print_formatted_text(value, *args, end: str = "\n", **kwargs):
        del args, kwargs
        if isinstance(value, ANSI):
            print(value.value, end=end)
        else:
            print(value, end=end)

    monkeypatch.setattr(interactive, "run_in_terminal", fake_run_in_terminal)
    monkeypatch.setattr(interactive, "print_formatted_text", fake_print_formatted_text)
    monkeypatch.setattr("hahobot.cli.stream.estimate_text_tokens", lambda _text: 24)

    output = io.StringIO()
    with redirect_stdout(output):
        renderer = StreamRenderer(render_markdown=True, interactive=True, show_spinner=True)
        await renderer.on_delta("Hello")
        await renderer.on_delta(", **world**")
        await renderer.on_delta("!\n")
        renderer._segment_started_at = None
        renderer._stream_elapsed = 2.0
        await renderer.on_end()

    rendered = output.getvalue()
    assert "Hello" in rendered
    assert "\x1b[2K" not in rendered
    assert "\x1b[?25l" not in rendered
    assert "\x1b[?25h" not in rendered
    assert "≈12.0 tok/s" in _strip_ansi(rendered)


async def test_non_interactive_stream_renderer_still_uses_live_path(monkeypatch):
    class TTYStringIO(io.StringIO):
        def isatty(self) -> bool:
            return True

    output = TTYStringIO()
    monkeypatch.setattr("hahobot.cli.stream.estimate_text_tokens", lambda _text: 10)
    with redirect_stdout(output):
        renderer = StreamRenderer(render_markdown=True, interactive=False, show_spinner=False)
        await renderer.on_delta("Hello from Live")
        assert renderer._live is not None
        renderer._segment_started_at = None
        renderer._stream_elapsed = 2.0
        await renderer.on_end()

    assert "≈5.0 tok/s" in _strip_ansi(output.getvalue())


async def test_resuming_stream_defers_rate_until_final_segment(monkeypatch):
    printed_lines: list[str] = []

    async def fake_response(*_args, **_kwargs):
        return None

    async def fake_line(text: str):
        printed_lines.append(text)

    monkeypatch.setattr(interactive, "_print_interactive_response", fake_response)
    monkeypatch.setattr(interactive, "_print_interactive_line", fake_line)
    monkeypatch.setattr("hahobot.cli.stream.estimate_text_tokens", lambda _text: 30)

    renderer = StreamRenderer(render_markdown=True, interactive=True, show_spinner=False)
    await renderer.on_delta("tool preface")
    renderer._segment_started_at = None
    renderer._stream_elapsed = 1.0
    await renderer.on_end(resuming=True)
    assert printed_lines == []

    await renderer.on_delta("final answer")
    renderer._segment_started_at = None
    renderer._stream_elapsed = 3.0
    await renderer.on_end()

    assert printed_lines == ["⚡ ≈10.0 tok/s · ≈30 tok · 3.0s"]
    assert renderer.rate_metrics() == (30, 3.0)
