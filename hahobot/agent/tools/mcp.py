"""MCP client: connects to MCP servers and wraps their tools as native hahobot tools."""

import asyncio
import hashlib
import urllib.parse
from contextlib import AsyncExitStack
from typing import Any, TextIO

import httpx
from loguru import logger

from hahobot.agent.tools.base import Tool
from hahobot.agent.tools.registry import ToolRegistry
from hahobot.security.network import validate_resolved_url


def _redact_url(url: str) -> str:
    """Strip credentials and query/fragment before logging an MCP URL.

    Server URLs may embed secrets (``https://user:token@host/sse`` or a
    ``?token=`` query). Some deployments also put opaque tokens in the path, so
    log only the origin and a path placeholder. Ported from nanobot ``780093d0``
    / ``bfc2a74e`` / ``f9b02496``.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        hostname = parts.hostname or ""
        netloc = f"[{hostname}]" if ":" in hostname else hostname
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        path = "/..." if parts.path and parts.path != "/" else parts.path
        return urllib.parse.urlunsplit((parts.scheme, netloc, path, "", ""))
    except Exception:
        return "<redacted-url>"


def _make_mcp_redirect_validator(configured_url: str):
    """Build a request event hook that blocks redirects to internal addresses.

    Unlike the web tools, an MCP server URL is *operator-configured* in
    ``config.json``, so the configured host is trusted even when it is loopback
    or LAN — local MCP servers (e.g. ``http://127.0.0.1:3211/mcp``) are the
    common case and must keep working.  The only SSRF vector left is a
    *configured-public* server that redirects to an internal address (e.g. cloud
    metadata).  httpx follows redirects internally for the HTTP/SSE transports,
    so this hook runs on every hop: it allows requests to the configured host
    and rejects only a redirect to a *different* host that resolves to a
    private/internal address.
    """
    try:
        configured_host = httpx.URL(configured_url).host
    except Exception:
        configured_host = None

    async def _validate(request: httpx.Request) -> None:
        if configured_host and request.url.host == configured_host:
            return  # operator-configured host (incl. localhost/LAN) is trusted
        ok, error = await validate_resolved_url(str(request.url))
        if not ok:
            raise httpx.RequestError(
                f"Blocked MCP redirect to unsafe URL {_redact_url(str(request.url))} ({error})",
                request=request,
            )

    return _validate


def _extract_nullable_branch(options: Any) -> tuple[dict[str, Any], bool] | None:
    """Return the single non-null branch for nullable unions."""
    if not isinstance(options, list):
        return None

    non_null: list[dict[str, Any]] = []
    saw_null = False
    for option in options:
        if not isinstance(option, dict):
            return None
        if option.get("type") == "null":
            saw_null = True
            continue
        non_null.append(option)

    if saw_null and len(non_null) == 1:
        return non_null[0], True
    return None


def _normalize_schema_for_openai(schema: Any) -> dict[str, Any]:
    """Normalize only nullable JSON Schema patterns for tool definitions."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    normalized = dict(schema)

    raw_type = normalized.get("type")
    if isinstance(raw_type, list):
        non_null = [item for item in raw_type if item != "null"]
        if "null" in raw_type and len(non_null) == 1:
            normalized["type"] = non_null[0]
            normalized["nullable"] = True

    for key in ("oneOf", "anyOf"):
        nullable_branch = _extract_nullable_branch(normalized.get(key))
        if nullable_branch is not None:
            branch, _ = nullable_branch
            merged = {k: v for k, v in normalized.items() if k != key}
            merged.update(branch)
            normalized = merged
            normalized["nullable"] = True
            break

    if "properties" in normalized and isinstance(normalized["properties"], dict):
        normalized["properties"] = {
            name: _normalize_schema_for_openai(prop) if isinstance(prop, dict) else prop
            for name, prop in normalized["properties"].items()
        }

    if "items" in normalized and isinstance(normalized["items"], dict):
        normalized["items"] = _normalize_schema_for_openai(normalized["items"])

    if normalized.get("type") != "object":
        return normalized

    normalized.setdefault("properties", {})
    normalized.setdefault("required", [])
    return normalized


# ── Session-termination detection ──────────────────────────────────────────────


def _is_session_terminated(exc: BaseException) -> bool:
    """Return True when the MCP SDK reports a dead client session."""
    messages = [str(exc)]
    error = getattr(exc, "error", None)
    if error is not None:
        messages.append(str(getattr(error, "message", "")))
    return any(
        marker in message.lower()
        for marker in ("session terminated", "connection closed")
        for message in messages
    )


# ── Base wrapper with reconnect support ────────────────────────────────────────


class _MCPWrapperBase(Tool):
    """Base for MCP wrappers that can recover from a terminated session."""

    def __init__(self, session):
        self._session = session
        self._coordinator: _MCPServerConnection | None = None
        self._generation: int = 0

    def _set_coordinator(self, coordinator: "_MCPServerConnection") -> None:
        """Attach this wrapper to a per-server connection coordinator."""
        self._coordinator = coordinator
        self._generation = coordinator._generation

    async def _refresh_session_after_termination(
        self,
        exc: BaseException,
        already_refreshed: bool,
        kind: str,
    ) -> bool:
        """Rebuild the session and retry if exc indicates a terminated session.

        Returns True when a fresh session was obtained and the caller should
        retry the current operation.  Fires at most once per execute() call
        (guarded by *already_refreshed*).
        """
        if already_refreshed or self._coordinator is None or not _is_session_terminated(exc):
            return False
        ok = await self._coordinator.reconnect(self)
        if ok:
            logger.info(
                "MCP {} '{}': reconnected after session termination",
                kind,
                self.name,
            )
            return True
        return False


# ── Per-server connection coordinator ──────────────────────────────────────────


class _MCPConnectionOwner:
    """Keep one MCP connection generation inside its owning asyncio task.

    MCP transports use AnyIO cancel scopes internally. Those scopes must be
    exited by the same task that entered them, so the owner task stays alive
    for the full lifetime of its local ``AsyncExitStack``. Other tasks only
    request closure and await the owner; they never close the transport stack.
    """

    def __init__(self, name: str, cfg: Any, *, discover_tools: bool) -> None:
        loop = asyncio.get_running_loop()
        self.name = name
        self.cfg = cfg
        self._discover_tools = discover_tools
        self._ready: asyncio.Future[tuple[Any, Any | None]] = loop.create_future()
        self._close_requested = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"mcp:{name}:owner")

    async def _run(self) -> None:
        try:
            async with AsyncExitStack() as local:
                # asyncio.timeout keeps _open_session in this owner task.
                # asyncio.wait_for would wrap the coroutine in another task and
                # recreate the cross-task AnyIO cancel-scope bug on timeout.
                async with asyncio.timeout(self.cfg.connect_timeout):
                    session = await _open_session(self.name, self.cfg, local)
                    tools = await session.list_tools() if self._discover_tools else None

                if not self._ready.done():
                    self._ready.set_result((session, tools))
                await self._close_requested.wait()
        except BaseException as exc:
            if not self._ready.done():
                if isinstance(exc, asyncio.CancelledError):
                    self._ready.cancel()
                else:
                    self._ready.set_exception(exc)
            elif not isinstance(exc, asyncio.CancelledError):
                logger.warning(
                    "MCP server '{}': connection owner exited with {}: {}",
                    self.name,
                    type(exc).__name__,
                    exc,
                )

    async def wait_ready(self) -> tuple[Any, Any | None]:
        """Wait until the owner has opened the session (without owning it)."""
        return await asyncio.shield(self._ready)

    async def close(self) -> None:
        """Request same-task cleanup and wait for it; safe to call repeatedly."""
        self._close_requested.set()
        if not self._ready.done():
            # A generation still blocked in startup must be interrupted so its
            # owner can unwind the local stack immediately in the same task.
            self._task.cancel()
        if self._task is asyncio.current_task():
            return
        try:
            await asyncio.shield(self._task)
        except asyncio.CancelledError:
            # Propagate cancellation of the caller, but tolerate a cancelled
            # owner task after startup was explicitly aborted.
            caller = asyncio.current_task()
            if (caller is not None and caller.cancelling() > 0) or not self._task.cancelled():
                raise


async def _open_connection_owner(
    name: str,
    cfg: Any,
    *,
    discover_tools: bool,
) -> tuple[_MCPConnectionOwner, Any, Any | None]:
    """Start one task-owned connection generation and wait for readiness."""
    owner = _MCPConnectionOwner(name, cfg, discover_tools=discover_tools)
    try:
        session, tools = await owner.wait_ready()
    except BaseException:
        # The caller can be cancelled independently of the owner task. Always
        # stop the owner here so failed/cancelled startup cannot leak resources.
        await owner.close()
        raise
    return owner, session, tools


class _MCPServerConnection:
    """Manages one MCP server connection with generation-based concurrency safety.

    When several wrappers of the same server detect a terminated session at
    nearly the same time, exactly one rebuild happens; the others adopt the
    freshly-rebuilt session via the generation counter.
    """

    def __init__(
        self,
        name: str,
        cfg: Any,
        stack: AsyncExitStack,
        owner: _MCPConnectionOwner,
        session: Any,
        wrappers: list[_MCPWrapperBase],
    ):
        self.name = name
        self.cfg = cfg
        self.stack = stack
        self._lock = asyncio.Lock()
        self._generation = 0
        self._owner = owner
        self._session = session
        self._wrappers = wrappers

    async def reconnect(self, wrapper: _MCPWrapperBase) -> bool:
        """Rebuild the server session, or adopt the already-rebuilt one.

        Returns True when the wrapper now has a live session (either freshly
        built by this call or inherited from a concurrent rebuild).
        """
        async with self._lock:
            # Another caller already rebuilt – just adopt the live session.
            if wrapper._generation < self._generation:
                wrapper._session = self._session
                wrapper._generation = self._generation
                return True

            # We are the first caller for this termination – rebuild.
            try:
                new_owner, new_session, _tools = await _open_connection_owner(
                    self.name,
                    self.cfg,
                    discover_tools=False,
                )
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
                    raise
                logger.warning(
                    "MCP server '{}': reconnect was cancelled by SDK, keeping old session",
                    self.name,
                )
                return False
            except Exception:
                logger.warning("MCP server '{}': reconnect failed, keeping old session", self.name)
                return False

            try:
                # Shared cleanup owns only task-safe owner.close callbacks.
                self.stack.push_async_callback(new_owner.close)
            except Exception:
                await new_owner.close()
                logger.warning("MCP server '{}': reconnect cleanup registration failed", self.name)
                return False

            old_owner = self._owner
            self._generation += 1
            self._owner = new_owner
            self._session = new_session
            for w in self._wrappers:
                w._session = new_session
                # Do NOT bump w._generation here — the generation guard
                # (w._generation < self._generation) distinguishes callers
                # that predate the rebuild from those that arrive after.
                # Bumping all wrappers would make every caller equal to
                # the coordinator again, defeating the guard.
            logger.info(
                "MCP server '{}': reconnected (generation {})",
                self.name,
                self._generation,
            )
            # Wrappers now target the replacement, so the prior generation can
            # close its transport stack from the task that opened it.
            await old_owner.close()
            return True


# ── Session factory (shared by initial connect and reconnect) ──────────────────


async def _open_session(name: str, cfg: Any, stack: AsyncExitStack) -> Any:
    """Open an MCP ClientSession for one server on *stack* (transport + init).

    This is the single place that knows how to select and wire up stdio / SSE /
    streamableHttp transports.  Both the initial connect loop and the reconnect
    path call here so the transport-selection logic stays in one place.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    transport_type = cfg.type
    if not transport_type:
        if cfg.command:
            transport_type = "stdio"
        elif cfg.url:
            # Convention: URLs ending with /sse use SSE transport; others use streamableHttp
            transport_type = "sse" if cfg.url.rstrip("/").endswith("/sse") else "streamableHttp"
        else:
            raise ValueError("no command or url configured")

    if transport_type == "stdio":
        params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.env or None)
        errlog = _stderr_to_logger(name, stack)
        read, write = await stack.enter_async_context(
            stdio_client(params, errlog=errlog) if errlog is not None else stdio_client(params)
        )
        _close_writer_best_effort(errlog)
    elif transport_type == "sse":

        def httpx_client_factory(
            headers: dict[str, str] | None = None,
            timeout: httpx.Timeout | None = None,
            auth: httpx.Auth | None = None,
            _cfg_headers: dict[str, str] | None = cfg.headers,
        ) -> httpx.AsyncClient:
            merged_headers = {
                "Accept": "application/json, text/event-stream",
                **(_cfg_headers or {}),
                **(headers or {}),
            }
            return httpx.AsyncClient(
                headers=merged_headers or None,
                event_hooks={"request": [_make_mcp_redirect_validator(cfg.url)]},
                follow_redirects=True,
                timeout=timeout,
                auth=auth,
            )

        read, write = await stack.enter_async_context(
            sse_client(cfg.url, httpx_client_factory=httpx_client_factory)
        )
    elif transport_type == "streamableHttp":
        # Always provide an explicit httpx client so MCP HTTP transport does not
        # inherit httpx's default 5s timeout and preempt the higher-level tool timeout.
        http_client = await stack.enter_async_context(
            httpx.AsyncClient(
                headers=cfg.headers or None,
                event_hooks={"request": [_make_mcp_redirect_validator(cfg.url)]},
                follow_redirects=True,
                timeout=None,
            )
        )
        read, write, _ = await stack.enter_async_context(
            streamable_http_client(cfg.url, http_client=http_client)
        )
    else:
        raise ValueError(f"unknown transport type '{transport_type}'")

    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


# ── Stdio MCP subprocess stderr capture ───────────────────────────────────────


def _close_writer_best_effort(writer: TextIO | None) -> None:
    """Best-effort close of the stderr pipe *writer* end.

    This is split out so _open_session does not repeat the try/except.
    """
    if writer is None:
        return
    try:
        writer.close()
    except OSError:
        pass


def _stderr_to_logger(name: str, stack: AsyncExitStack) -> TextIO | None:
    """Return a writable pipe end to use as the MCP subprocess stderr.

    A daemon thread drains the read end and forwards each line to loguru at
    debug level, so stdio MCP server stderr does not pollute the interactive
    CLI.  Returns None on any failure so the caller falls back to sys.stderr
    and MCP startup is never blocked.
    """
    import os
    import threading

    try:
        r_fd, w_fd = os.pipe()
        reader = os.fdopen(r_fd, "r", encoding="utf-8", errors="replace")
        writer = os.fdopen(w_fd, "w", buffering=1, encoding="utf-8", errors="replace")
    except Exception:
        logger.debug("MCP server '{}': failed to create stderr pipe, falling back", name)
        return None

    def _drain() -> None:
        """Read lines from the pipe and forward them to loguru."""
        try:
            for line in reader:
                stripped = line.rstrip()
                if stripped:
                    logger.debug("[mcp:{}] {}", name, stripped)
        except Exception:
            # Reader EOF or any I/O/logging error — thread exits cleanly.
            pass

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()

    # Register cleanup: close the read end so the thread unblocks and exits.
    def _cleanup() -> None:
        try:
            reader.close()
        except OSError:
            pass

    try:
        stack.callback(_cleanup)
    except Exception:
        reader.close()
        writer.close()
        return None

    return writer


# ── Wrapper classes ────────────────────────────────────────────────────────────


_MAX_TOOL_NAME_LENGTH = 64
_TOOL_NAME_HASH_LENGTH = 8


def _limit_tool_name(name: str, max_length: int = _MAX_TOOL_NAME_LENGTH) -> str:
    """Cap a model-facing MCP tool name at the Anthropic 64-char tool-name limit.

    Short names are returned unchanged. A longer ``mcp_<server>_<tool>`` name is
    truncated and given a stable sha1 suffix so two distinct long names cannot
    collide onto one tool name — an over-length name otherwise 400s the Anthropic
    Messages API and bricks the session (the same session-bricking class as the
    duplicate/invalid tool-id guards). The MCP server is always called with the
    wrapper's ``_original_name``, so capping the local name never affects dispatch.
    Ported from nanobot `3f9fb63d` (length core only; hahobot's reconnect/unregister
    matches on the `_MCPServerConnection` coordinator, not a name prefix).
    """
    if len(name) <= max_length:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:_TOOL_NAME_HASH_LENGTH]
    prefix_length = max_length - _TOOL_NAME_HASH_LENGTH - 1
    return f"{name[:prefix_length]}_{digest}"


class MCPToolWrapper(_MCPWrapperBase):
    """Wraps a single MCP server tool as a hahobot Tool."""

    def __init__(self, session, server_name: str, tool_def, tool_timeout: int = 30):
        super().__init__(session)
        self._original_name = tool_def.name
        self._name = _limit_tool_name(f"mcp_{server_name}_{tool_def.name}")
        self._description = tool_def.description or tool_def.name
        raw_schema = tool_def.inputSchema or {"type": "object", "properties": {}}
        self._parameters = _normalize_schema_for_openai(raw_schema)
        self._tool_timeout = tool_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types

        refreshed_session = False
        while True:
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(self._original_name, arguments=kwargs),
                    timeout=self._tool_timeout,
                )
            except TimeoutError:
                logger.warning("MCP tool '{}' timed out after {}s", self._name, self._tool_timeout)
                return f"(MCP tool call timed out after {self._tool_timeout}s)"
            except asyncio.CancelledError:
                # MCP SDK's anyio cancel scopes can leak CancelledError on timeout/failure.
                # Re-raise only if our task was externally cancelled (e.g. /stop).
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
                    raise
                logger.warning("MCP tool '{}' was cancelled by server/SDK", self._name)
                return "(MCP tool call was cancelled)"
            except Exception as exc:
                if await self._refresh_session_after_termination(exc, refreshed_session, "tool"):
                    refreshed_session = True
                    continue
                logger.exception(
                    "MCP tool '{}' failed: {}: {}",
                    self._name,
                    type(exc).__name__,
                    exc,
                )
                return f"(MCP tool call failed: {type(exc).__name__})"

            parts = []
            for block in result.content:
                if isinstance(block, types.TextContent):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return "\n".join(parts) or "(no output)"


class MCPResourceWrapper(_MCPWrapperBase):
    """Wraps an MCP resource URI as a read-only hahobot Tool."""

    def __init__(self, session, server_name: str, resource_def, resource_timeout: int = 30):
        super().__init__(session)
        self._uri = resource_def.uri
        self._name = f"mcp_{server_name}_resource_{resource_def.name}"
        desc = resource_def.description or resource_def.name
        self._description = f"[MCP Resource] {desc}\nURI: {self._uri}"
        self._parameters: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        self._resource_timeout = resource_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types

        refreshed_session = False
        while True:
            try:
                result = await asyncio.wait_for(
                    self._session.read_resource(self._uri),
                    timeout=self._resource_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "MCP resource '{}' timed out after {}s", self._name, self._resource_timeout
                )
                return f"(MCP resource read timed out after {self._resource_timeout}s)"
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
                    raise
                logger.warning("MCP resource '{}' was cancelled by server/SDK", self._name)
                return "(MCP resource read was cancelled)"
            except Exception as exc:
                if await self._refresh_session_after_termination(
                    exc, refreshed_session, "resource"
                ):
                    refreshed_session = True
                    continue
                logger.exception(
                    "MCP resource '{}' failed: {}: {}",
                    self._name,
                    type(exc).__name__,
                    exc,
                )
                return f"(MCP resource read failed: {type(exc).__name__})"

            parts: list[str] = []
            for block in result.contents:
                if isinstance(block, types.TextResourceContents):
                    parts.append(block.text)
                elif isinstance(block, types.BlobResourceContents):
                    parts.append(f"[Binary resource: {len(block.blob)} bytes]")
                else:
                    parts.append(str(block))
            return "\n".join(parts) or "(no output)"


class MCPPromptWrapper(_MCPWrapperBase):
    """Wraps an MCP prompt as a read-only hahobot Tool."""

    def __init__(self, session, server_name: str, prompt_def, prompt_timeout: int = 30):
        super().__init__(session)
        self._prompt_name = prompt_def.name
        self._name = f"mcp_{server_name}_prompt_{prompt_def.name}"
        desc = prompt_def.description or prompt_def.name
        self._description = (
            f"[MCP Prompt] {desc}\n"
            "Returns a filled prompt template that can be used as a workflow guide."
        )
        self._prompt_timeout = prompt_timeout

        # Build parameters from prompt arguments
        properties: dict[str, Any] = {}
        required: list[str] = []
        for arg in prompt_def.arguments or []:
            prop: dict[str, Any] = {"type": "string"}
            if getattr(arg, "description", None):
                prop["description"] = arg.description
            properties[arg.name] = prop
            if arg.required:
                required.append(arg.name)
        self._parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types
        from mcp.shared.exceptions import McpError

        refreshed_session = False
        while True:
            try:
                result = await asyncio.wait_for(
                    self._session.get_prompt(self._prompt_name, arguments=kwargs),
                    timeout=self._prompt_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "MCP prompt '{}' timed out after {}s", self._name, self._prompt_timeout
                )
                return f"(MCP prompt call timed out after {self._prompt_timeout}s)"
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
                    raise
                logger.warning("MCP prompt '{}' was cancelled by server/SDK", self._name)
                return "(MCP prompt call was cancelled)"
            except McpError as exc:
                logger.error(
                    "MCP prompt '{}' failed: code={} message={}",
                    self._name,
                    exc.error.code,
                    exc.error.message,
                )
                return f"(MCP prompt call failed: {exc.error.message} [code {exc.error.code}])"
            except Exception as exc:
                if await self._refresh_session_after_termination(exc, refreshed_session, "prompt"):
                    refreshed_session = True
                    continue
                logger.exception(
                    "MCP prompt '{}' failed: {}: {}",
                    self._name,
                    type(exc).__name__,
                    exc,
                )
                return f"(MCP prompt call failed: {type(exc).__name__})"

            parts: list[str] = []
            for message in result.messages:
                content = message.content
                # content is a single ContentBlock (not a list) in MCP SDK >= 1.x
                if isinstance(content, types.TextContent):
                    parts.append(content.text)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, types.TextContent):
                            parts.append(block.text)
                        else:
                            parts.append(str(block))
                else:
                    parts.append(str(content))
            return "\n".join(parts) or "(no output)"


# ── Connection entry point ─────────────────────────────────────────────────────


async def _connect_one_mcp(
    name: str,
    cfg: Any,
) -> tuple[str, Any, _MCPConnectionOwner, Any, Any] | None:
    """Connect one MCP server through a task-owned connection generation."""
    try:
        owner, session, tools_result = await _open_connection_owner(
            name,
            cfg,
            discover_tools=True,
        )
        return name, cfg, owner, session, tools_result
    except TimeoutError:
        logger.error(
            "MCP server '{}': connect timed out after {}s; skipping",
            name,
            cfg.connect_timeout,
        )
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            # Re-raise only if genuinely externally cancelled.
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            # SDK-leaked CancelledError -- treat as a connect failure.
            logger.error(
                "MCP server '{}': connect was cancelled by SDK; skipping",
                name,
            )
        elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        else:
            logger.error("MCP server '{}': failed to connect: {}", name, exc)
    return None


async def connect_mcp_servers(
    mcp_servers: dict, registry: ToolRegistry, stack: AsyncExitStack
) -> None:
    """Connect to configured MCP servers concurrently with per-server timeouts.

    Phase 1: connect every server concurrently. Each server gets a long-lived
    owner task whose local ``AsyncExitStack`` is bounded by
    ``asyncio.timeout(cfg.connect_timeout)``, so a hung server cannot block
    others and every MCP/AnyIO context exits from the task that entered it.

    Phase 2: register successfully connected servers sequentially, preserving
    the original ``mcp_servers`` insertion order for deterministic logs.
    """
    if not mcp_servers:
        return

    # Phase 1 -- concurrent connect -------------------------------------------------
    coros = [_connect_one_mcp(name, cfg) for name, cfg in mcp_servers.items()]
    results = await asyncio.gather(*coros)

    # Phase 2 -- sequential registration in insertion order -------------------------
    for (name, cfg), result in zip(mcp_servers.items(), results, strict=False):
        if result is None:
            # Connect phase already logged the reason; just skip.
            continue

        _server_name, _cfg, owner, session, tools = result
        registered: list[tuple[_MCPWrapperBase, Tool | None]] = []
        try:
            enabled_tools = set(cfg.enabled_tools)
            allow_all_tools = "*" in enabled_tools
            matched_enabled_tools: set[str] = set()
            available_raw_names = [tool_def.name for tool_def in tools.tools]
            available_wrapped_names = [
                _limit_tool_name(f"mcp_{name}_{tool_def.name}") for tool_def in tools.tools
            ]
            registered_count = 0

            # Collect wrappers for this server before registering, so the
            # coordinator can track them all for session-reconnect broadcasts.
            wrappers: list[_MCPWrapperBase] = []
            for tool_def in tools.tools:
                wrapped_name = _limit_tool_name(f"mcp_{name}_{tool_def.name}")
                if (
                    not allow_all_tools
                    and tool_def.name not in enabled_tools
                    and wrapped_name not in enabled_tools
                ):
                    logger.debug(
                        "MCP: skipping tool '{}' from server '{}' (not in enabledTools)",
                        wrapped_name,
                        name,
                    )
                    continue
                wrapper = MCPToolWrapper(session, name, tool_def, tool_timeout=cfg.tool_timeout)
                wrappers.append(wrapper)
                if enabled_tools:
                    if tool_def.name in enabled_tools:
                        matched_enabled_tools.add(tool_def.name)
                    if wrapped_name in enabled_tools:
                        matched_enabled_tools.add(wrapped_name)

            if enabled_tools and not allow_all_tools:
                unmatched_enabled_tools = sorted(enabled_tools - matched_enabled_tools)
                if unmatched_enabled_tools:
                    logger.warning(
                        "MCP server '{}': enabledTools entries not found: {}. Available raw names: {}. "
                        "Available wrapped names: {}",
                        name,
                        ", ".join(unmatched_enabled_tools),
                        ", ".join(available_raw_names) or "(none)",
                        ", ".join(available_wrapped_names) or "(none)",
                    )

            coordinator = _MCPServerConnection(name, cfg, stack, owner, session, wrappers)
            for wrapper in wrappers:
                wrapper._set_coordinator(coordinator)

            # The shared stack owns only the task-safe close request. The
            # owner's local stack continues to own every MCP SDK context.
            stack.push_async_callback(owner.close)

            for wrapper in wrappers:
                previous = registry.get(wrapper.name)
                registered.append((wrapper, previous))
                registry.register(wrapper)
                logger.debug("MCP: registered tool '{}' from server '{}'", wrapper.name, name)
                registered_count += 1

            logger.info("MCP server '{}': connected, {} tools registered", name, registered_count)
        except Exception:
            logger.exception("MCP server '{}': registration failed", name)
            for wrapper, previous in reversed(registered):
                try:
                    if registry.get(wrapper.name) is not wrapper:
                        continue
                    if previous is None:
                        registry.unregister(wrapper.name)
                    else:
                        registry.register(previous)
                except Exception:
                    logger.warning(
                        "MCP server '{}': failed to roll back tool '{}' registration",
                        name,
                        wrapper.name,
                    )
            await owner.close()
