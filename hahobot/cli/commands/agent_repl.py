"""The interactive `agent` CLI command."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

import typer
from loguru import logger

from hahobot import __logo__
from hahobot.cli.commands import interactive, runtime
from hahobot.cli.commands._app import app
from hahobot.cli.commands.interactive import (
    _clear_interactive_completion_context,
    _cli_route_for_session,
    _flush_pending_tty_input,
    _handle_local_compact_command,
    _handle_local_repo_command,
    _handle_local_review_command,
    _handle_local_session_command,
    _init_prompt_session,
    _is_exit_command,
    _print_cli_progress_line,
    _print_interactive_progress_line,
    _print_interactive_response,
    _read_interactive_input_async,
    _restore_terminal,
    _sanitize_surrogates,
    _select_session_interactively,
    _set_interactive_completion_context,
    _update_interactive_completion_session,
    console,
)
from hahobot.cli.session_stats import CliSessionStats
from hahobot.cli.stream import StreamRenderer, ThinkingSpinner
from hahobot.config.paths import is_default_workspace
from hahobot.utils.restart import (
    consume_restart_notice_from_env,
    format_restart_completed_message,
    should_show_cli_restart_notice,
)


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str | None = typer.Option(None, "--session", "-s", help="Session ID"),
    continue_last: bool = typer.Option(
        False, "--continue", help="Resume the most recent CLI session"
    ),
    pick_session: bool = typer.Option(
        False, "--pick-session", help="Interactively choose a recent CLI session"
    ),
    multiline: bool = typer.Option(
        False,
        "--multiline",
        help="Enable multiline input in interactive mode (Enter newline, Ctrl+J submit)",
    ),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    markdown: bool = typer.Option(
        True, "--markdown/--no-markdown", help="Render assistant output as Markdown"
    ),
    logs: bool = typer.Option(
        False, "--logs/--no-logs", help="Show hahobot runtime logs during chat"
    ),
):
    """Interact with the agent directly."""

    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus
    from hahobot.cli.session_inspector import list_session_summaries, pick_recent_cli_session_key
    from hahobot.config.loader import get_config_path
    from hahobot.cron.service import CronService
    from hahobot.session.manager import SessionManager

    config_arg = config
    config = runtime._load_runtime_config(config_arg, workspace)
    runtime_config_path = (
        Path(config_arg).expanduser().resolve() if config_arg else get_config_path()
    )
    runtime.sync_workspace_templates(config.workspace_path)

    session_manager = SessionManager(config.workspace_path)
    if sum([session_id is not None, continue_last, pick_session]) > 1:
        console.print(
            "[red]Error: choose only one of --session, --continue, or --pick-session.[/red]"
        )
        raise typer.Exit(1)
    if session_id is None:
        if continue_last:
            session_id = pick_recent_cli_session_key(session_manager)
            if session_id is not None:
                console.print(f"[dim]Resuming session: {session_id}[/dim]")
            else:
                session_id = "cli:direct"
                console.print("[dim]No previous CLI session found. Starting cli:direct.[/dim]")
        elif pick_session:
            sessions = list_session_summaries(session_manager, cli_only=True, limit=20)
            session_id = _select_session_interactively(sessions)
            if session_id == "cli:direct":
                console.print("[dim]Starting new session: cli:direct[/dim]")
            else:
                console.print(f"[dim]Selected session: {session_id}[/dim]")
        else:
            session_id = "cli:direct"

    bus = MessageBus()
    provider = runtime._make_provider(config)

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        runtime._migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path, max_sleep_ms=config.gateway.cron.max_sleep_ms)

    if logs:
        logger.enable("hahobot")
    else:
        logger.disable("hahobot")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        config_path=runtime_config_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        tool_hint_max_length=config.agents.defaults.tool_hint_max_length,
        brave_api_key=config.tools.web.search.api_key or None,
        web_proxy=config.tools.web.proxy or None,
        web_search_provider=config.tools.web.search.provider,
        web_search_base_url=config.tools.web.search.base_url or None,
        web_search_max_results=config.tools.web.search.max_results,
        exec_config=config.tools.exec,
        image_gen_config=config.tools.image_gen,
        memory_config=config.memory,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=config.agents.defaults.timezone,
        unified_session=config.agents.defaults.unified_session,
        session_ttl_minutes=config.agents.defaults.session_ttl_minutes,
        disabled_skills=config.agents.defaults.disabled_skills,
    )
    restart_notice = consume_restart_notice_from_env()
    if restart_notice and should_show_cli_restart_notice(restart_notice, session_id):
        interactive._print_agent_response(
            format_restart_completed_message(restart_notice.started_at_raw),
            render_markdown=False,
        )

    # Shared reference for progress callbacks
    _thinking: ThinkingSpinner | None = None

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        _print_cli_progress_line(content, _thinking)

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            renderer = StreamRenderer(render_markdown=markdown)
            response = await agent_loop.process_direct(
                message,
                session_id,
                on_progress=_cli_progress,
                on_stream=renderer.on_delta,
                on_stream_end=renderer.on_end,
            )
            if not renderer.streamed:
                await renderer.close()
                interactive._print_agent_response(
                    response.content if response else "",
                    render_markdown=markdown,
                    metadata=response.metadata if response else None,
                )
            await agent_loop.close_mcp()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from hahobot.bus.events import InboundMessage

        _set_interactive_completion_context(
            workspace=config.workspace_path,
            session_manager=session_manager,
            current_session_id=session_id,
        )
        _init_prompt_session()
        console.print(
            f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)"
        )
        if multiline:
            console.print(
                "[dim]Multiline input enabled: Enter inserts newline, Ctrl+J submits.[/dim]"
            )
        console.print()

        cli_channel, cli_chat_id = _cli_route_for_session(session_id)
        session_stats = CliSessionStats()
        summary_printed = False
        usage_observer = session_stats.record_usage
        usage_observer_attached = False
        add_usage_observer = getattr(provider, "add_usage_observer", None)
        exit_state: dict[str, str | None] = {"message": None}

        def _print_session_summary() -> None:
            nonlocal summary_printed
            if summary_printed:
                return
            summary_printed = True
            _restore_terminal()
            console.print()
            console.print("[bold cyan]Session summary[/bold cyan]")
            turn_count = getattr(agent_loop, "_usage_turn_count", 0)
            use_observed_usage = usage_observer_attached and (
                session_stats.model_calls > 0 or turn_count == 0
            )
            for line in session_stats.summary_lines(
                usage=None if use_observed_usage else getattr(agent_loop, "_usage_totals", {}),
                turn_count=turn_count,
            ):
                console.print(f"  [dim]{line}[/dim]")

        def _begin_exit(message: str) -> None:
            exit_state["message"] = message
            _restore_terminal()
            console.print("\n[dim]Exiting, finalizing background work...[/dim]")

        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            _begin_exit(f"Received {sig_name}, goodbye!")
            sys.exit(0)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[tuple[str, dict]] = []
            renderer: StreamRenderer | None = None
            state = {
                "session_id": session_id,
                "cli_channel": cli_channel,
                "cli_chat_id": cli_chat_id,
            }

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

                        if msg.metadata.get("_stream_delta"):
                            if renderer:
                                await renderer.on_delta(msg.content)
                            continue
                        if msg.metadata.get("_stream_end"):
                            if renderer:
                                await renderer.on_end(
                                    resuming=msg.metadata.get("_resuming", False),
                                )
                            continue
                        if msg.metadata.get("_streamed"):
                            if msg.content and not (renderer and renderer.streamed):
                                meta = dict(msg.metadata or {})
                                meta.pop("_streamed", None)
                                turn_response.append((msg.content, meta))
                            turn_done.set()
                            continue

                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                await _print_interactive_progress_line(msg.content, _thinking)
                            continue

                        if not turn_done.is_set():
                            if msg.content:
                                turn_response.append((msg.content, dict(msg.metadata or {})))
                            turn_done.set()
                        elif msg.content:
                            await _print_interactive_response(
                                msg.content,
                                render_markdown=markdown,
                                metadata=msg.metadata,
                            )

                    except TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        # Stop spinner before user input to avoid prompt_toolkit conflicts
                        if renderer:
                            renderer.stop_for_input()
                        user_input = _sanitize_surrogates(
                            await _read_interactive_input_async(multiline=multiline)
                        )
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _begin_exit("Goodbye!")
                            break

                        if command.startswith("/session"):
                            result = _handle_local_session_command(
                                command,
                                session_manager=session_manager,
                                current_session_id=str(state["session_id"]),
                            )
                            if result.new_session_id is not None:
                                state["session_id"] = result.new_session_id
                                _update_interactive_completion_session(result.new_session_id)
                                next_channel, next_chat_id = _cli_route_for_session(
                                    result.new_session_id
                                )
                                state["cli_channel"] = next_channel
                                state["cli_chat_id"] = next_chat_id
                            await _print_interactive_response(
                                result.text,
                                render_markdown=False,
                                metadata={"render_as": "text"},
                            )
                            continue

                        if command.startswith("/repo"):
                            result = _handle_local_repo_command(
                                command,
                                workspace=config.workspace_path,
                            )
                            await _print_interactive_response(
                                result.text,
                                render_markdown=False,
                                metadata={"render_as": "text"},
                            )
                            continue

                        if command.startswith("/review"):
                            result = await _handle_local_review_command(
                                command,
                                provider=provider,
                                model=config.agents.defaults.model,
                                workspace=config.workspace_path,
                                retry_mode=config.agents.defaults.provider_retry_mode,
                            )
                            await _print_interactive_response(
                                result.text,
                                render_markdown=markdown,
                            )
                            continue

                        if command.startswith("/compact"):
                            result = await _handle_local_compact_command(
                                command,
                                loop=agent_loop,
                                current_session_id=str(state["session_id"]),
                            )
                            await _print_interactive_response(
                                result.text,
                                render_markdown=False,
                                metadata={"render_as": "text"},
                            )
                            continue

                        turn_done.clear()
                        turn_response.clear()
                        renderer = StreamRenderer(render_markdown=markdown, interactive=True)

                        await bus.publish_inbound(
                            InboundMessage(
                                channel=str(state["cli_channel"]),
                                sender_id="user",
                                chat_id=str(state["cli_chat_id"]),
                                content=user_input,
                                metadata={"_wants_stream": True},
                                session_key_override=str(state["session_id"]),
                            )
                        )

                        await turn_done.wait()

                        if renderer:
                            session_stats.record_stream(*renderer.rate_metrics())

                        if turn_response:
                            content, meta = turn_response[0]
                            if content and not meta.get("_streamed"):
                                if renderer:
                                    await renderer.close()
                                interactive._print_agent_response(
                                    content,
                                    render_markdown=markdown,
                                    metadata=meta,
                                )
                        elif renderer and not renderer.streamed:
                            await renderer.close()
                    except KeyboardInterrupt:
                        _begin_exit("Goodbye!")
                        break
                    except EOFError:
                        _begin_exit("Goodbye!")
                        break
            finally:
                _clear_interactive_completion_context()
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()

        try:
            if callable(add_usage_observer):
                try:
                    add_usage_observer(usage_observer)
                    usage_observer_attached = True
                except Exception as exc:
                    logger.debug("Could not attach CLI usage observer: {}", exc)
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
            # SIGHUP is not available on Windows
            if hasattr(signal, "SIGHUP"):
                signal.signal(signal.SIGHUP, _handle_signal)
            # Ignore SIGPIPE to prevent silent process termination when writing to closed pipes
            # SIGPIPE is not available on Windows
            if hasattr(signal, "SIGPIPE"):
                signal.signal(signal.SIGPIPE, signal.SIG_IGN)
            asyncio.run(run_interactive())
        finally:
            if usage_observer_attached:
                remove_usage_observer = getattr(provider, "remove_usage_observer", None)
                if callable(remove_usage_observer):
                    try:
                        remove_usage_observer(usage_observer)
                    except Exception as exc:
                        logger.debug("Could not remove CLI usage observer: {}", exc)
            try:
                _print_session_summary()
            except Exception as exc:
                logger.debug("Could not print CLI session summary: {}", exc)
            if exit_state["message"]:
                console.print(f"\n{exit_state['message']}")
