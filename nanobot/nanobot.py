"""High-level compatibility facade for legacy nanobot SDK imports."""

from __future__ import annotations

from hahobot.hahobot import Hahobot, RunResult, _make_provider


class Nanobot(Hahobot):
    """Backward-compatible SDK facade alias for hahobot."""


__all__ = ["Nanobot", "RunResult", "_make_provider"]
