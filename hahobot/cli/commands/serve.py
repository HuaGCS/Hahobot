"""`serve` and `gateway` CLI commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from loguru import logger

from hahobot import __logo__, __version__
from hahobot.cli.commands import runtime
from hahobot.cli.commands._app import app
from hahobot.cli.commands.interactive import console
from hahobot.config.paths import is_default_workspace


@app.command()
def serve(
    port: int | None = typer.Option(None, "--port", "-p", help="API server port"),
    host: str | None = typer.Option(None, "--host", "-H", help="Bind address"),
    timeout: float | None = typer.Option(
        None, "--timeout", "-t", help="Per-request timeout (seconds)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show hahobot runtime logs"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the OpenAI-compatible API server (/v1/chat/completions)."""
    try:
        from aiohttp import web  # noqa: F401
    except ImportError:
        console.print("[red]aiohttp is required. Install with: pip install -e '.[api]'[/red]")
        raise typer.Exit(1) from None

    from loguru import logger

    from hahobot.agent.loop import AgentLoop
    from hahobot.api.server import create_app
    from hahobot.bus.queue import MessageBus
    from hahobot.session.manager import SessionManager

    if verbose:
        logger.enable("hahobot")
    else:
        logger.disable("hahobot")

    runtime_config = runtime._load_runtime_config(config, workspace)
    api_cfg = runtime_config.api
    host = host if host is not None else api_cfg.host
    port = port if port is not None else api_cfg.port
    timeout = timeout if timeout is not None else api_cfg.timeout
    runtime.sync_workspace_templates(runtime_config.workspace_path)
    bus = MessageBus()
    provider = runtime._make_provider(runtime_config)
    session_manager = SessionManager(runtime_config.workspace_path)
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=runtime_config.workspace_path,
        model=runtime_config.agents.defaults.model,
        max_iterations=runtime_config.agents.defaults.max_tool_iterations,
        context_window_tokens=runtime_config.agents.defaults.context_window_tokens,
        context_block_limit=runtime_config.agents.defaults.context_block_limit,
        max_tool_result_chars=runtime_config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=runtime_config.agents.defaults.provider_retry_mode,
        tool_hint_max_length=runtime_config.agents.defaults.tool_hint_max_length,
        web_config=runtime_config.tools.web,
        exec_config=runtime_config.tools.exec,
        restrict_to_workspace=runtime_config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=runtime_config.tools.mcp_servers,
        channels_config=runtime_config.channels,
        timezone=runtime_config.agents.defaults.timezone,
        unified_session=runtime_config.agents.defaults.unified_session,
        session_ttl_minutes=runtime_config.agents.defaults.session_ttl_minutes,
        disabled_skills=runtime_config.agents.defaults.disabled_skills,
    )

    model_name = runtime_config.agents.defaults.model
    console.print(f"{__logo__} Starting OpenAI-compatible API server")
    console.print(f"  [cyan]Endpoint[/cyan] : http://{host}:{port}/v1/chat/completions")
    console.print(f"  [cyan]Model[/cyan]    : {model_name}")
    console.print("  [cyan]Session[/cyan]  : api:default")
    console.print(f"  [cyan]Timeout[/cyan]  : {timeout}s")
    auth_key = (api_cfg.auth_key or "").strip()
    if host in {"0.0.0.0", "::"}:
        if not auth_key:
            console.print(
                "[red]Error:[/red] API is bound to all interfaces "
                f"({host}) but api.authKey is not set. Set api.authKey in config "
                "to prevent unauthenticated network access."
            )
            raise typer.Exit(1)
        console.print(
            "[yellow]Warning:[/yellow] API is bound to all interfaces "
            "(authentication required). Only do this behind a trusted network "
            "boundary, firewall, or reverse proxy."
        )
    console.print()
    if runtime_config.a2a.enabled:
        console.print(f"  [cyan]A2A[/cyan]      : http://{host}:{port}/.well-known/agent-card.json")

    api_app = create_app(
        agent_loop,
        model_name=model_name,
        request_timeout=timeout,
        host=host,
        port=port,
        a2a_config=runtime_config.a2a,
        auth_key=auth_key,
    )

    async def on_startup(_app):
        await agent_loop._connect_mcp()

    async def on_cleanup(_app):
        await agent_loop.close_mcp()

    api_app.on_startup.append(on_startup)
    api_app.on_cleanup.append(on_cleanup)

    web.run_app(api_app, host=host, port=port, print=lambda msg: logger.info(msg))


@app.command()
def gateway(
    port: int | None = typer.Option(None, "--port", "-p", help="Gateway port"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the hahobot gateway."""
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus
    from hahobot.channels.manager import ChannelManager
    from hahobot.config.loader import get_config_path, load_config
    from hahobot.cron.service import CronService
    from hahobot.cron.types import CronJob
    from hahobot.gateway.http import GatewayHttpServer
    from hahobot.gateway.runtime_status import GatewayRuntimeStatusTracker, GatewayStatusHook
    from hahobot.heartbeat.service import HeartbeatService
    from hahobot.session.manager import SessionManager
    from hahobot.star_office import StarOfficeHook, StarOfficePushSettings, StarOfficeStatusTracker

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    config_arg = config
    config = runtime._load_runtime_config(config_arg, workspace)
    runtime_config_path = (
        Path(config_arg).expanduser().resolve() if config_arg else get_config_path()
    )
    port = port if port is not None else config.gateway.port

    console.print(f"{__logo__} Starting hahobot gateway version {__version__} on port {port}...")
    runtime.sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    provider = runtime._make_provider(config)
    session_manager = SessionManager(config.workspace_path)
    star_office_tracker = StarOfficeStatusTracker(
        push_settings=StarOfficePushSettings.from_status_config(config.gateway.status)
    )
    runtime_status_tracker = GatewayRuntimeStatusTracker(model=config.agents.defaults.model)

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        runtime._migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path, max_sleep_ms=config.gateway.cron.max_sleep_ms)

    # Create agent with cron service
    agent = AgentLoop(
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
        hooks=[StarOfficeHook(star_office_tracker), GatewayStatusHook(runtime_status_tracker)],
        unified_session=config.agents.defaults.unified_session,
        session_ttl_minutes=config.agents.defaults.session_ttl_minutes,
        disabled_skills=config.agents.defaults.disabled_skills,
    )
    runtime_status_tracker.set_model(agent.model)

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        # Dream is an internal job — run directly, not through the agent loop.
        if job.name == "dream":
            try:
                await agent.dream.run()
                logger.info("Dream cron job completed")
            except Exception:
                logger.exception("Dream cron job failed")
            return None

        from hahobot.agent.tools.cron import CronTool
        from hahobot.agent.tools.message import MessageTool
        from hahobot.utils.evaluator import evaluate_response

        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        # Prevent the agent from scheduling new cron jobs during execution
        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            resp = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        response = resp.content if resp else ""

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            should_notify = await evaluate_response(
                response,
                reminder_note,
                provider,
                agent.model,
            )
            if should_notify:
                from hahobot.bus.events import OutboundMessage

                outbound = OutboundMessage(
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to,
                    content=response,
                )
                await bus.publish_outbound(outbound)
                await agent._record_proactive_delivery(outbound)
        return response

    cron.on_job = on_cron_job

    # Create channel manager
    channels = ChannelManager(config, bus)

    # WebUI proactive-push wiring: one shared broadcaster feeds both the /app/ws
    # connection registry and a `webui` pseudo-channel injected into the manager,
    # so cron/heartbeat/message-tool output routed to channel="webui" reaches live
    # clients (and is persisted into the webui:<id> session for offline reload).
    webui_broadcaster = None
    if config.gateway.webui.enabled:
        from hahobot.gateway.webui.broadcast import WebUIBroadcaster
        from hahobot.gateway.webui.channel import WebUIChannel

        webui_broadcaster = WebUIBroadcaster()
        channels.channels["webui"] = WebUIChannel(webui_broadcaster, bus, config.workspace_path)

    async def _reload_runtime_state() -> None:
        """Force-reload runtime-configurable state after admin config saves."""
        reloaded = load_config(runtime_config_path)
        runtime.sync_workspace_templates(reloaded.workspace_path, silent=True)
        cron.rebind_store(reloaded.workspace_path / "cron" / "jobs.json")
        cron.apply_runtime_config(reloaded.gateway.cron.max_sleep_ms)
        await agent.reload_runtime_config(reloaded)
        runtime_status_tracker.set_model(agent.model)
        channels.apply_runtime_config(reloaded)
        star_office_tracker.apply_push_settings(
            StarOfficePushSettings.from_status_config(reloaded.gateway.status)
        )
        await heartbeat.apply_runtime_config(
            workspace=reloaded.workspace_path,
            model=agent.model,
            interval_s=reloaded.gateway.heartbeat.interval_s,
            enabled=reloaded.gateway.heartbeat.enabled,
            timezone=reloaded.agents.defaults.timezone,
        )
        http_server.update_runtime_workspace(reloaded.workspace_path)

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        resp = await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

        # Keep a small tail of heartbeat history so the loop stays bounded
        # without losing all short-term context between runs.
        session = agent.sessions.get_or_create("heartbeat")
        session.retain_recent_legal_suffix(hb_cfg.keep_recent_messages)
        agent.sessions.save(session)

        return resp.content if resp else ""

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from hahobot.bus.events import OutboundMessage

        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        outbound = OutboundMessage(channel=channel, chat_id=chat_id, content=response)
        await bus.publish_outbound(outbound)
        await agent._record_proactive_delivery(outbound)

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
        timezone=config.agents.defaults.timezone,
    )

    http_server = GatewayHttpServer(
        config.gateway.host,
        port,
        config_path=runtime_config_path,
        workspace=config.workspace_path,
        reload_runtime=_reload_runtime_state,
        star_office_tracker=star_office_tracker,
        runtime_status_tracker=runtime_status_tracker,
        heartbeat_service=heartbeat,
        subagent_manager=agent.subagents,
        agent=agent,
        session_manager=session_manager,
        webui_broadcaster=webui_broadcaster,
        webui_cron_service=cron if config.gateway.webui.enabled else None,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    # Register Dream system job (always-on, idempotent on restart)
    dream_cfg = config.agents.defaults.dream
    if dream_cfg.model_override:
        agent.dream.model = dream_cfg.model_override
    agent.dream.max_batch_size = dream_cfg.max_batch_size
    agent.dream.max_iterations = dream_cfg.max_iterations
    from hahobot.cron.types import CronPayload

    cron.register_system_job(
        CronJob(
            id="dream",
            name="dream",
            schedule=dream_cfg.build_schedule(config.agents.defaults.timezone),
            payload=CronPayload(kind="system_event"),
        )
    )
    console.print(f"[green]✓[/green] Dream: {dream_cfg.describe_schedule()}")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            await http_server.start()
            star_office_tracker.publish_current()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await http_server.stop()
            await channels.stop_all()
            await star_office_tracker.aclose()

    asyncio.run(run())
