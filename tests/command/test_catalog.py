from __future__ import annotations

import pytest

from hahobot.agent.commands import router as router_mod
from hahobot.agent.i18n import help_lines
from hahobot.bus.events import InboundMessage, OutboundMessage
from hahobot.command.catalog import (
    admin_command_specs,
    interactive_command_names,
    normalize_telegram_command_text,
    telegram_forwardable_commands,
)
from hahobot.command.router import CommandContext


def test_help_and_admin_catalog_include_gateway_workspace_commands() -> None:
    help_text = "\n".join(help_lines("en"))
    admin_commands = [spec.command for spec in admin_command_specs()]

    assert "/dream-log" in help_text
    assert "/dream-restore" in help_text
    assert "/session current" in help_text
    assert "/repo <status|diff>" in help_text
    assert "/review [staged|base <rev>|path <repo-path>]" in help_text
    assert "/compact [key]" in help_text
    assert "/dream" in admin_commands
    assert "/dream-log" in admin_commands
    assert "/dream-restore" in admin_commands
    assert "/session" in admin_commands
    assert "/repo" in admin_commands
    assert "/review" in admin_commands
    assert "/compact" in admin_commands


def test_interactive_and_telegram_catalog_keep_aliases_and_safe_names() -> None:
    names = interactive_command_names()

    assert "/language" in names
    assert "/session" in names
    assert "/repo" in names
    assert "/review" in names
    assert "/compact" in names
    assert "language" in telegram_forwardable_commands()
    assert "dream_log" in telegram_forwardable_commands()
    assert "session" in telegram_forwardable_commands()
    assert "repo" in telegram_forwardable_commands()
    assert "review" in telegram_forwardable_commands()
    assert "compact" in telegram_forwardable_commands()
    assert normalize_telegram_command_text("/dream_log deadbeef") == "/dream-log deadbeef"
    assert normalize_telegram_command_text("/dream_restore deadbeef") == "/dream-restore deadbeef"


@pytest.mark.asyncio
async def test_agent_command_router_registers_dream_log_prefix(monkeypatch) -> None:
    seen: dict[str, str] = {}

    async def fake_dream_log(ctx: CommandContext) -> OutboundMessage:
        seen["raw"] = ctx.raw
        seen["args"] = ctx.args
        return OutboundMessage(channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, content="ok")

    monkeypatch.setattr(router_mod, "cmd_dream_log", fake_dream_log)
    router = router_mod.build_agent_command_router()
    ctx = CommandContext(
        msg=InboundMessage(channel="cli", sender_id="u1", chat_id="direct", content="/dream-log deadbeef"),
        session=None,
        key="cli:direct",
        raw="/dream-log deadbeef",
        loop=object(),
    )

    out = await router.dispatch(ctx)

    assert out is not None
    assert out.content == "ok"
    assert seen == {"raw": "/dream-log deadbeef", "args": "deadbeef"}
