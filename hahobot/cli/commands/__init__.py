"""CLI commands for hahobot.

This package was split out of a single ``commands.py`` module. ``app`` and a
few helpers stay importable from ``hahobot.cli.commands`` for the configured
entry point and for tests.
"""

import asyncio  # noqa: F401  -- keeps hahobot.cli.commands.asyncio.* patchable
import os
import sys

# Force UTF-8 encoding for Windows console (must run before prompt_toolkit).
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Import command modules for their side effect: @app.command() registration.
from hahobot.cli.commands import agent_repl, config_cmd, core, groups, serve  # noqa: F401
from hahobot.cli.commands._app import app
from hahobot.cli.commands.runtime import (
    _make_provider,
    _merge_missing_defaults,
    _migrate_cron_store,
)

__all__ = [
    "app",
    "_make_provider",
    "_merge_missing_defaults",
    "_migrate_cron_store",
]


if __name__ == "__main__":
    app()
