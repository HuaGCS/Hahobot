"""Tests for the consolidated /dream dispatcher.

`/dream` now dispatches based on its first sub-token: empty runs the
consolidation, `log [sha]` shows the latest (or specified) Dream diff, and
`restore [sha]` lists/restores versions. The legacy `/dream-log` and
`/dream-restore` aliases are still routed for backward compatibility, but
they no longer appear in /help, the admin reference, or the Telegram menu.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hahobot.bus.events import InboundMessage
from hahobot.command.builtin import cmd_dream
from hahobot.command.catalog import (
    admin_command_specs,
    help_command_specs,
    telegram_menu_specs,
)
from hahobot.command.router import CommandContext


def _ctx(args: str) -> CommandContext:
    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content=f"/dream {args}".strip(),
    )
    return CommandContext(
        msg=msg,
        session=None,
        key="cli:direct",
        raw=msg.content,
        args=args,
        loop=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_empty_args_routes_to_run(monkeypatch) -> None:
    run = AsyncMock(return_value="ran")
    monkeypatch.setattr("hahobot.command.builtin._cmd_dream_run", run)
    log = AsyncMock()
    monkeypatch.setattr("hahobot.command.builtin.cmd_dream_log", log)
    restore = AsyncMock()
    monkeypatch.setattr("hahobot.command.builtin.cmd_dream_restore", restore)

    result = await cmd_dream(_ctx(""))

    run.assert_awaited_once()
    log.assert_not_awaited()
    restore.assert_not_awaited()
    assert result == "ran"


@pytest.mark.asyncio
async def test_log_subcommand_routes_with_remaining_args(monkeypatch) -> None:
    log = AsyncMock(return_value="log")
    monkeypatch.setattr("hahobot.command.builtin.cmd_dream_log", log)
    run = AsyncMock()
    monkeypatch.setattr("hahobot.command.builtin._cmd_dream_run", run)

    await cmd_dream(_ctx("log deadbeef"))

    log.assert_awaited_once()
    forwarded_ctx = log.await_args.args[0]
    assert forwarded_ctx.args == "deadbeef"
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_log_subcommand_without_sha_passes_empty_args(monkeypatch) -> None:
    log = AsyncMock(return_value="log")
    monkeypatch.setattr("hahobot.command.builtin.cmd_dream_log", log)

    await cmd_dream(_ctx("log"))

    forwarded_ctx = log.await_args.args[0]
    assert forwarded_ctx.args == ""


@pytest.mark.asyncio
async def test_restore_subcommand_routes_with_remaining_args(monkeypatch) -> None:
    restore = AsyncMock(return_value="restored")
    monkeypatch.setattr("hahobot.command.builtin.cmd_dream_restore", restore)

    await cmd_dream(_ctx("restore abcd1234"))

    restore.assert_awaited_once()
    forwarded_ctx = restore.await_args.args[0]
    assert forwarded_ctx.args == "abcd1234"


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage(monkeypatch) -> None:
    run = AsyncMock()
    monkeypatch.setattr("hahobot.command.builtin._cmd_dream_run", run)
    log = AsyncMock()
    monkeypatch.setattr("hahobot.command.builtin.cmd_dream_log", log)
    restore = AsyncMock()
    monkeypatch.setattr("hahobot.command.builtin.cmd_dream_restore", restore)

    result = await cmd_dream(_ctx("wat"))

    run.assert_not_awaited()
    log.assert_not_awaited()
    restore.assert_not_awaited()
    assert "Usage" in result.content
    assert "/dream log" in result.content


def test_legacy_dream_aliases_are_hidden_from_user_facing_menus() -> None:
    help_commands = {spec.command for spec in help_command_specs()}
    admin_commands = {spec.command for spec in admin_command_specs()}
    menu_commands = {spec.command for spec in telegram_menu_specs()}

    assert "/dream" in help_commands
    assert "/dream-log" not in help_commands
    assert "/dream-restore" not in help_commands

    assert "/dream" in admin_commands
    assert "/dream-log" not in admin_commands
    assert "/dream-restore" not in admin_commands

    assert "/dream" in menu_commands
    assert "/dream-log" not in menu_commands
    assert "/dream-restore" not in menu_commands
