"""``openclaw`` CLI alias.

Convenience entry point so OpenClaw-style commands work verbatim, e.g.::

    openclaw config set skills.entries.today-task.config.authCode <value>

It runs the same Typer app as ``hahobot`` (only the program name differs), so
every ``hahobot`` subcommand is available under ``openclaw`` too.
"""

from __future__ import annotations

from hahobot.cli.commands import app


def main() -> None:
    app(prog_name="openclaw")


if __name__ == "__main__":
    main()
