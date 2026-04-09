"""Legacy entry point for running nanobot as a module or script."""

from __future__ import annotations

from hahobot.cli.commands import app


def main() -> None:
    app(prog_name="nanobot")


if __name__ == "__main__":
    main()
