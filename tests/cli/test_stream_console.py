"""Regression: the CLI console must not force ANSI escapes to a non-TTY sink.

Forcing ANSI (the previous ``force_terminal=True``) leaked the live spinner's
redraw frames as literal ``\\x1b[2K`` junk whenever stdout was piped/captured.
"""

import io

from hahobot.cli import stream


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_console_does_not_force_ansi_when_not_a_tty(monkeypatch):
    monkeypatch.setattr(stream.sys, "stdout", io.StringIO())  # isatty() -> False
    console = stream._make_console()
    assert console._force_terminal is None


def test_console_forces_ansi_on_a_real_tty(monkeypatch):
    monkeypatch.setattr(stream.sys, "stdout", _TTY())
    console = stream._make_console()
    assert console._force_terminal is True
