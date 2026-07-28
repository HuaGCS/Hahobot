"""Typer application and sub-command groups for the hahobot CLI."""

import typer

from hahobot import __logo__

app = typer.Typer(
    name="hahobot",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"{__logo__} hahobot - Personal AI Assistant",
    no_args_is_help=True,
)

persona_app = typer.Typer(help="Manage personas")
companion_app = typer.Typer(help="Companion workflow utilities")
sessions_app = typer.Typer(help="Inspect saved sessions")
repo_app = typer.Typer(help="Inspect local repository state")
memory_app = typer.Typer(help="Manage memory")
memory_index_app = typer.Typer(help="Manage archive search indexes")
memory_shared_app = typer.Typer(help="Manage optional shared memory")
channels_app = typer.Typer(help="Manage channels")
plugins_app = typer.Typer(help="Manage channel plugins")
provider_app = typer.Typer(help="Manage providers")
config_app = typer.Typer(
    help="Read/write per-skill config (skills.entries) in <workspace>/skills.json"
)

app.add_typer(persona_app, name="persona")
app.add_typer(companion_app, name="companion")
app.add_typer(sessions_app, name="sessions")
app.add_typer(repo_app, name="repo")
app.add_typer(memory_app, name="memory")
memory_app.add_typer(memory_index_app, name="index")
memory_app.add_typer(memory_shared_app, name="shared")
app.add_typer(channels_app, name="channels")
app.add_typer(plugins_app, name="plugins")
app.add_typer(provider_app, name="provider")
app.add_typer(config_app, name="config")
