"""AgentLoop slash-command router registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hahobot.command.builtin import cmd_dream, cmd_dream_log, cmd_dream_restore
from hahobot.command.catalog import agent_command_specs
from hahobot.command.router import CommandContext, CommandRouter

if TYPE_CHECKING:
    from hahobot.bus.events import OutboundMessage


def _session(ctx: CommandContext):
    return ctx.session or ctx.loop.sessions.get_or_create(ctx.key)


async def _cmd_status(ctx: CommandContext) -> OutboundMessage:
    session = _session(ctx)
    return ctx.loop._system_commands.status(ctx.msg, session)


async def _cmd_new(ctx: CommandContext) -> OutboundMessage:
    session = _session(ctx)
    language = ctx.loop._get_session_language(session)
    return ctx.loop._system_commands.new_session(ctx.msg, session, language)


async def _cmd_help(ctx: CommandContext) -> OutboundMessage:
    session = _session(ctx)
    language = ctx.loop._get_session_language(session)
    return ctx.loop._system_commands.help(ctx.msg, language)


async def _cmd_lang(ctx: CommandContext):
    return await ctx.loop._handle_language_command(ctx.msg, _session(ctx))


async def _cmd_persona(ctx: CommandContext):
    return await ctx.loop._handle_persona_command(ctx.msg, _session(ctx))


async def _cmd_stchar(ctx: CommandContext):
    return await ctx.loop._handle_stchar_command(ctx.msg, _session(ctx))


async def _cmd_preset(ctx: CommandContext):
    return await ctx.loop._handle_preset_command(ctx.msg, _session(ctx))


async def _cmd_scene(ctx: CommandContext):
    return await ctx.loop._handle_scene_command(ctx.msg, _session(ctx))


async def _cmd_skill(ctx: CommandContext):
    return await ctx.loop._handle_skill_command(ctx.msg, _session(ctx))


async def _cmd_mcp(ctx: CommandContext):
    return await ctx.loop._handle_mcp_command(ctx.msg, _session(ctx))


async def _cmd_session(ctx: CommandContext):
    return await ctx.loop._workspace_commands.session(ctx.msg, _session(ctx), ctx.args)


async def _cmd_repo(ctx: CommandContext):
    return ctx.loop._workspace_commands.repo(ctx.msg, ctx.args)


async def _cmd_review(ctx: CommandContext):
    return await ctx.loop._workspace_commands.review(ctx.msg, ctx.args)


async def _cmd_compact(ctx: CommandContext):
    return await ctx.loop._workspace_commands.compact(ctx.msg, _session(ctx), ctx.args)


async def _cmd_stop_priority(ctx: CommandContext):
    await ctx.loop._handle_stop(ctx.msg)
    return None


async def _cmd_restart_priority(ctx: CommandContext):
    await ctx.loop._handle_restart(ctx.msg)
    return None


def build_agent_command_router() -> CommandRouter:
    """Create the slash-command router used by AgentLoop."""
    router = CommandRouter()
    handlers = {
        "/new": _cmd_new,
        "/lang": _cmd_lang,
        "/persona": _cmd_persona,
        "/stchar": _cmd_stchar,
        "/preset": _cmd_preset,
        "/scene": _cmd_scene,
        "/skill": _cmd_skill,
        "/mcp": _cmd_mcp,
        "/stop": _cmd_stop_priority,
        "/restart": _cmd_restart_priority,
        "/status": _cmd_status,
        "/dream": cmd_dream,
        "/dream-log": cmd_dream_log,
        "/dream-restore": cmd_dream_restore,
        "/help": _cmd_help,
        "/session": _cmd_session,
        "/repo": _cmd_repo,
        "/review": _cmd_review,
        "/compact": _cmd_compact,
    }

    for spec in agent_command_specs():
        handler = handlers.get(spec.command)
        if handler is None:
            continue
        for form in spec.forms():
            if spec.priority:
                router.priority(form, handler)
            router.exact(form, handler)
            if spec.prefix_match:
                router.prefix(f"{form} ", handler)

    return router
