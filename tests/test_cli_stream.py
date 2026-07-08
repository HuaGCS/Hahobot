from __future__ import annotations

import io
from contextlib import redirect_stdout

from prompt_toolkit.formatted_text import ANSI

from hahobot.cli.commands import interactive
from hahobot.cli.stream import StreamRenderer


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

    output = io.StringIO()
    with redirect_stdout(output):
        renderer = StreamRenderer(render_markdown=True, interactive=True, show_spinner=True)
        await renderer.on_delta("Hello")
        await renderer.on_delta(", **world**")
        await renderer.on_delta("!\n")
        await renderer.on_end()

    rendered = output.getvalue()
    assert "Hello" in rendered
    assert "\x1b[2K" not in rendered
    assert "\x1b[?25l" not in rendered
    assert "\x1b[?25h" not in rendered


async def test_non_interactive_stream_renderer_still_uses_live_path():
    class TTYStringIO(io.StringIO):
        def isatty(self) -> bool:
            return True

    output = TTYStringIO()
    with redirect_stdout(output):
        renderer = StreamRenderer(render_markdown=True, interactive=False, show_spinner=False)
        await renderer.on_delta("Hello from Live")
        assert renderer._live is not None
        await renderer.close()
