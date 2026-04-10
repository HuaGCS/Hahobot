"""Canonical slash-command metadata shared across runtime surfaces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    """Metadata for one slash command family."""

    command: str
    description_keys: tuple[str, ...]
    usage_lines: tuple[str, ...] = ()
    usage_text_key: str | None = None
    aliases: tuple[str, ...] = ()
    note_key: str | None = None
    completion_subcommands: tuple[str, ...] = ()
    agent_enabled: bool = True
    help_enabled: bool = True
    admin_enabled: bool = True
    interactive_enabled: bool = True
    telegram_menu_enabled: bool = True
    prefix_match: bool = False
    priority: bool = False
    help_rank: int = 0
    admin_rank: int = 0
    interactive_rank: int = 0
    telegram_rank: int = 0

    def forms(self) -> tuple[str, ...]:
        """Return canonical command plus aliases."""
        return (self.command, *self.aliases)

    def telegram_name(self) -> str:
        """Return a Telegram-safe command name."""
        return telegram_safe_command_name(self.command)


_COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(
        command="/new",
        description_keys=("cmd_new",),
        usage_lines=("/new",),
        help_rank=10,
        admin_rank=30,
        interactive_rank=10,
        telegram_rank=20,
    ),
    CommandSpec(
        command="/lang",
        description_keys=("cmd_lang_current", "cmd_lang_list", "cmd_lang_set"),
        usage_lines=(
            "/lang current",
            "/lang list",
            "/lang set <en|zh>",
        ),
        aliases=("/language",),
        note_key="admin_commands_note_lang",
        completion_subcommands=("current", "list", "set"),
        prefix_match=True,
        help_rank=20,
        admin_rank=40,
        interactive_rank=40,
        telegram_rank=30,
    ),
    CommandSpec(
        command="/persona",
        description_keys=("cmd_persona_current", "cmd_persona_list", "cmd_persona_set"),
        usage_lines=(
            "/persona current",
            "/persona list",
            "/persona set <name>",
        ),
        note_key="admin_commands_note_persona",
        completion_subcommands=("current", "list", "set"),
        prefix_match=True,
        help_rank=30,
        admin_rank=50,
        interactive_rank=50,
        telegram_rank=40,
    ),
    CommandSpec(
        command="/stchar",
        description_keys=("cmd_stchar",),
        usage_lines=(
            "/stchar list",
            "/stchar show <name>",
            "/stchar load <name>",
        ),
        completion_subcommands=("list", "show", "load"),
        prefix_match=True,
        help_rank=40,
        admin_rank=60,
        interactive_rank=60,
        telegram_rank=50,
    ),
    CommandSpec(
        command="/preset",
        description_keys=("cmd_preset",),
        usage_lines=(
            "/preset",
            "/preset show",
            "/preset show <persona>",
        ),
        completion_subcommands=("show",),
        prefix_match=True,
        help_rank=50,
        admin_rank=70,
        interactive_rank=70,
        telegram_rank=60,
    ),
    CommandSpec(
        command="/scene",
        description_keys=("cmd_scene",),
        usage_lines=(
            "/scene list",
            "/scene daily",
            "/scene comfort",
            "/scene date",
            "/scene <custom_scene>",
            "/scene generate <brief>",
        ),
        completion_subcommands=("list", "generate"),
        prefix_match=True,
        help_rank=60,
        admin_rank=80,
        interactive_rank=80,
        telegram_rank=70,
    ),
    CommandSpec(
        command="/skill",
        description_keys=("cmd_skill",),
        usage_text_key="skill_usage",
        note_key="admin_commands_note_skill",
        completion_subcommands=("search", "install", "uninstall", "list", "update"),
        prefix_match=True,
        help_rank=70,
        admin_rank=90,
        interactive_rank=90,
        telegram_rank=80,
    ),
    CommandSpec(
        command="/mcp",
        description_keys=("cmd_mcp",),
        usage_text_key="mcp_usage",
        note_key="admin_commands_note_mcp",
        completion_subcommands=("list",),
        prefix_match=True,
        help_rank=80,
        admin_rank=100,
        interactive_rank=100,
        telegram_rank=90,
    ),
    CommandSpec(
        command="/stop",
        description_keys=("cmd_stop",),
        usage_lines=("/stop",),
        note_key="admin_commands_note_stop",
        priority=True,
        help_rank=90,
        admin_rank=130,
        interactive_rank=140,
        telegram_rank=100,
    ),
    CommandSpec(
        command="/restart",
        description_keys=("cmd_restart",),
        usage_lines=("/restart",),
        note_key="admin_commands_note_restart",
        priority=True,
        help_rank=100,
        admin_rank=140,
        interactive_rank=150,
        telegram_rank=110,
    ),
    CommandSpec(
        command="/status",
        description_keys=("cmd_status",),
        usage_lines=("/status",),
        priority=True,
        help_rank=110,
        admin_rank=20,
        interactive_rank=20,
        telegram_rank=120,
    ),
    CommandSpec(
        command="/dream",
        description_keys=("cmd_dream",),
        usage_lines=("/dream",),
        help_rank=120,
        admin_rank=110,
        interactive_rank=110,
        telegram_rank=130,
    ),
    CommandSpec(
        command="/dream-log",
        description_keys=("cmd_dream_log",),
        usage_lines=(
            "/dream-log",
            "/dream-log <sha>",
        ),
        prefix_match=True,
        help_rank=130,
        admin_rank=120,
        interactive_rank=120,
        telegram_rank=140,
    ),
    CommandSpec(
        command="/dream-restore",
        description_keys=("cmd_dream_restore",),
        usage_lines=(
            "/dream-restore",
            "/dream-restore <sha>",
        ),
        prefix_match=True,
        help_rank=140,
        admin_rank=125,
        interactive_rank=130,
        telegram_rank=150,
    ),
    CommandSpec(
        command="/help",
        description_keys=("cmd_help",),
        usage_lines=("/help",),
        help_rank=150,
        admin_rank=10,
        interactive_rank=30,
        telegram_rank=160,
    ),
    CommandSpec(
        command="/session",
        description_keys=(),
        usage_lines=(
            "/session current",
            "/session list",
            "/session show [key]",
            "/session use <key>",
            "/session new [name]",
        ),
        completion_subcommands=("current", "list", "show", "use", "new"),
        agent_enabled=False,
        help_enabled=False,
        admin_enabled=False,
        telegram_menu_enabled=False,
        interactive_rank=160,
    ),
)


def telegram_safe_command_name(command: str) -> str:
    """Convert a slash command into Telegram's safe command form."""
    return command.lstrip("/").replace("-", "_")


def agent_command_specs() -> tuple[CommandSpec, ...]:
    """Return commands routed by AgentLoop."""
    return tuple(spec for spec in _COMMAND_SPECS if spec.agent_enabled)


def help_command_specs() -> tuple[CommandSpec, ...]:
    """Return commands shown in user help text."""
    specs = [spec for spec in _COMMAND_SPECS if spec.help_enabled]
    return tuple(sorted(specs, key=lambda spec: spec.help_rank))


def admin_command_specs() -> tuple[CommandSpec, ...]:
    """Return commands shown in the admin command reference."""
    specs = [spec for spec in _COMMAND_SPECS if spec.admin_enabled]
    return tuple(sorted(specs, key=lambda spec: spec.admin_rank))


def interactive_command_specs() -> tuple[CommandSpec, ...]:
    """Return commands visible to the local interactive CLI."""
    specs = [spec for spec in _COMMAND_SPECS if spec.interactive_enabled]
    return tuple(sorted(specs, key=lambda spec: spec.interactive_rank))


def telegram_menu_specs() -> tuple[CommandSpec, ...]:
    """Return commands exposed in Telegram's native command menu."""
    specs = [spec for spec in _COMMAND_SPECS if spec.telegram_menu_enabled]
    return tuple(sorted(specs, key=lambda spec: spec.telegram_rank))


def interactive_command_names() -> tuple[str, ...]:
    """Return canonical and alias command names for local CLI completion."""
    names: list[str] = []
    for spec in interactive_command_specs():
        names.extend(spec.forms())
    return tuple(names)


def interactive_subcommands() -> dict[str, tuple[str, ...]]:
    """Return static subcommand completions keyed by command form."""
    mapping: dict[str, tuple[str, ...]] = {}
    for spec in interactive_command_specs():
        if not spec.completion_subcommands:
            continue
        for form in spec.forms():
            mapping[form] = spec.completion_subcommands
    return mapping


def telegram_forwardable_commands() -> tuple[str, ...]:
    """Return Telegram command names that should route through AgentLoop."""
    names: list[str] = []
    seen: set[str] = set()
    for spec in agent_command_specs():
        if spec.command == "/help":
            continue
        for form in spec.forms():
            safe = telegram_safe_command_name(form)
            if safe in seen:
                continue
            seen.add(safe)
            names.append(safe)
    return tuple(names)


def normalize_telegram_command_text(content: str) -> str:
    """Map Telegram-safe command names back to canonical slash commands."""
    if not content.startswith("/"):
        return content
    for spec in agent_command_specs():
        for form in spec.forms():
            safe = f"/{telegram_safe_command_name(form)}"
            if content == safe or content.startswith(safe + " "):
                return form + content[len(safe):]
    return content
