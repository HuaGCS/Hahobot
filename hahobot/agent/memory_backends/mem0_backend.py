"""Shared Mem0 REST backend with a durable local outbox and recall snapshot."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger

from hahobot.agent.memory_backends.base import UserMemoryBackend
from hahobot.agent.memory_models import MemoryCommitRequest, MemoryScope, ResolvedMemoryContext
from hahobot.agent.memory_shared_sqlite import SharedMemorySQLiteState
from hahobot.agent.personas import DEFAULT_PERSONA, normalize_persona_name
from hahobot.agent.privacy import strip_persona_private_text, strip_private_text

if TYPE_CHECKING:
    from hahobot.config.schema import SharedMemoryConfig


_STATE_VERSION = 1
_SNAPSHOT_LIMIT = 1_000
# One in-flight event per claim keeps the 120-second SQLite lease safely above
# the configured per-request timeout (at most 60 seconds) without a heartbeat.
_DRAIN_BATCH_SIZE = 1
_MAX_MEMORY_ITEM_CHARS = 500
_TOKEN_RE = re.compile(r"[a-z0-9_]{2,}|[\u3400-\u9fff]", re.IGNORECASE)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Mem0SharedMemoryBackend(UserMemoryBackend):
    """Augment local memory through a central self-hosted Mem0 REST server."""

    def __init__(
        self,
        config: SharedMemoryConfig,
        *,
        state_root: Path,
        schedule_background: Callable[[Awaitable[None]], asyncio.Task[Any]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        namespace: str = "global",
        write_mode: str | None = None,
    ) -> None:
        self._config = config
        self._schedule_background = schedule_background
        self._transport = transport
        self._namespace = namespace
        self._write_mode = write_mode or config.global_write_mode
        service = f"{config.base_url.rstrip('/')}|{config.user_id.strip()}"
        service_key = hashlib.sha256(service.encode("utf-8")).hexdigest()[:16]
        self._state = SharedMemorySQLiteState(state_root.expanduser() / service_key)
        self._drain_task: asyncio.Task[Any] | None = None
        self._snapshot_task: asyncio.Task[Any] | None = None
        self._retry_handle: asyncio.TimerHandle | None = None
        self._started = False
        self._retired = False
        self._closed = False
        self._retire_after_drain = False
        self._drain_requested = False

        if config.enabled and not self.configured:
            missing = []
            if not config.base_url.strip():
                missing.append("baseUrl")
            if not config.user_id.strip():
                missing.append("userId")
            logger.warning(
                "Shared Mem0 memory is enabled but not configured; missing memory.shared.{}",
                ", memory.shared.".join(missing),
            )

    @property
    def read_enabled(self) -> bool:
        return bool(self._config.enabled and self._config.read_enabled)

    @property
    def write_enabled(self) -> bool:
        return bool(
            self._config.enabled and self._config.write_enabled and self._write_mode != "off"
        )

    @property
    def configured(self) -> bool:
        return bool(self._config.base_url.strip() and self._config.user_id.strip())

    @property
    def state_path(self) -> Path:
        """Expose the local shared-memory state path for diagnostics and tests."""
        return self._state.db_path

    async def resolve_context(self, scope: MemoryScope) -> ResolvedMemoryContext:
        if not self.read_enabled or not self.configured:
            return ResolvedMemoryContext(source="mem0")

        if self.write_enabled:
            self._schedule_outbox_drain()

        query = self._sanitize_text(scope.query or "")
        if not query:
            cached = await self._snapshot_search(query="")
            # Initialize/read the local state before the background refresh opens
            # a second connection. This removes the common same-process first-use
            # WAL race while _connect also protects cross-process initialization.
            self._schedule_snapshot_refresh()
            return ResolvedMemoryContext(
                block=self._format_context(cached),
                source="mem0-cache" if cached else "mem0",
            )

        try:
            results = await self._search_remote(query)
        except Exception as exc:
            logger.warning("Mem0 shared-memory search failed; using local snapshot: {}", exc)
            cached = await self._snapshot_search(query=query)
            return ResolvedMemoryContext(
                block=self._format_context(cached),
                source="mem0-cache" if cached else "mem0",
            )

        try:
            await asyncio.to_thread(self._state.merge_snapshot, results)
        except Exception:
            logger.exception("Failed to update the local Mem0 recall snapshot")
        self._schedule_snapshot_refresh()
        return ResolvedMemoryContext(block=self._format_context(results), source="mem0")

    async def commit_turn(self, request: MemoryCommitRequest) -> None:
        if not self.write_enabled or not self.configured:
            return
        if self._write_mode == "user_only":
            request = replace(request, outbound_content=None, persisted_messages=())
        event = self._build_outbox_event(request)
        if event is None:
            return
        enqueue_task = asyncio.create_task(asyncio.to_thread(self._state.enqueue, event))
        try:
            await asyncio.shield(enqueue_task)
        except asyncio.CancelledError:
            # Preserve the enqueue-before-network contract even if the owning
            # turn is cancelled while SQLite is committing in a worker thread.
            await enqueue_task
            self._schedule_outbox_drain()
            raise
        self._schedule_outbox_drain()

    async def flush_session(self, scope: MemoryScope) -> None:
        if self.write_enabled and self.configured and not self._retired:
            await self._drain_outbox(force=True)

    async def start(self) -> None:
        """Recover a persisted outbox after construction enters an event loop."""
        if self._started or self._retired or self._closed:
            return
        self._started = True
        if self.write_enabled and self.configured:
            self._schedule_outbox_drain()

    def retire(self) -> None:
        """Cancel generation-owned work while leaving queued rows durable."""
        if self._retired:
            return
        self._retired = True
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        for task in (self._drain_task, self._snapshot_task):
            if task is not None and not task.done():
                task.cancel()
        self._drain_requested = False

    def retire_after_pending_work(self) -> None:
        """Allow final old-generation writes one attempt, then stop all retries."""
        if self._retired:
            return
        self._retire_after_drain = True
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        if self._snapshot_task is not None and not self._snapshot_task.done():
            self._snapshot_task.cancel()
        if not self.write_enabled or not self.configured:
            self.retire()
            return
        # Always request one final verification drain. This covers the race
        # where an already-running empty drain observed no rows just before the
        # last leased turn committed its event.
        self._drain_requested = True
        self._schedule_outbox_drain()

    async def close(self) -> None:
        """Idempotently retire and await cancellation of background work."""
        if self._closed:
            return
        self.retire()
        tasks = [
            task
            for task in (self._drain_task, self._snapshot_task)
            if task is not None and not task.done()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._closed = True

    def _start_task(self, coro: Awaitable[None]) -> asyncio.Task[Any]:
        if self._schedule_background is not None:
            return self._schedule_background(coro)
        return asyncio.create_task(coro)

    def _schedule_outbox_drain(self) -> None:
        if self._retired or self._closed or not self.write_enabled or not self.configured:
            return
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_requested = True
            return
        self._drain_requested = False
        self._drain_task = self._start_task(self._drain_outbox())
        self._drain_task.add_done_callback(self._clear_drain_task)

    def _clear_drain_task(self, task: asyncio.Task[Any]) -> None:
        if self._drain_task is not task:
            return
        self._drain_task = None
        if self._drain_requested and not self._retired and not self._closed:
            self._drain_requested = False
            self._schedule_outbox_drain()
            return
        if self._retire_after_drain:
            self.retire()

    def _schedule_snapshot_refresh(self) -> None:
        if self._retired or self._closed or self._config.snapshot_refresh_seconds <= 0:
            return
        if self._snapshot_task is not None and not self._snapshot_task.done():
            return
        self._snapshot_task = self._start_task(self._refresh_snapshot())
        self._snapshot_task.add_done_callback(self._clear_snapshot_task)

    def _clear_snapshot_task(self, task: asyncio.Task[Any]) -> None:
        if self._snapshot_task is task:
            self._snapshot_task = None

    async def _search_remote(self, query: str) -> list[dict[str, Any]]:
        response = await self._request(
            "POST",
            "/search",
            json={
                "query": query,
                "top_k": self._config.top_k,
                "filters": {"user_id": self._config.user_id.strip()},
            },
        )
        return self._normalize_results(response)[: self._config.top_k]

    async def _refresh_snapshot(self) -> None:
        claim: tuple[str, float] | None = None
        claim_task: asyncio.Task[tuple[str, float] | None] | None = None
        try:
            claim_task = asyncio.create_task(
                asyncio.to_thread(
                    self._state.claim_snapshot_refresh,
                    self._config.snapshot_refresh_seconds,
                )
            )
            claim = await asyncio.shield(claim_task)
            if claim is None:
                return
            token, started_at = claim
            response = await self._request(
                "GET",
                "/memories",
                params={
                    "user_id": self._config.user_id.strip(),
                    "top_k": _SNAPSHOT_LIMIT,
                },
            )
            results = self._normalize_results(response)[:_SNAPSHOT_LIMIT]
            await asyncio.to_thread(
                self._state.complete_snapshot_refresh,
                token,
                started_at,
                results,
                prune_missing=len(results) < _SNAPSHOT_LIMIT,
            )
        except asyncio.CancelledError:
            # Cancellation does not stop a thread launched by to_thread(). Wait
            # for the short SQLite claim transaction so a just-committed token
            # cannot be orphaned until its lease expires.
            if claim is None and claim_task is not None:
                try:
                    claim = await claim_task
                except Exception:
                    claim = None
            if claim is not None:
                await asyncio.to_thread(self._state.abort_snapshot_refresh, claim[0])
            raise
        except Exception as exc:
            if claim is not None:
                await asyncio.to_thread(self._state.abort_snapshot_refresh, claim[0])
            logger.warning("Mem0 shared-memory snapshot refresh failed: {}", exc)

    async def _snapshot_search(self, *, query: str) -> list[dict[str, Any]]:
        try:
            items = await asyncio.to_thread(self._state.snapshot_items)
        except Exception:
            logger.exception("Failed to read the local Mem0 recall snapshot")
            return []
        if not query:
            return items[-self._config.top_k :]

        query_folded = query.casefold()
        tokens = set(_TOKEN_RE.findall(query_folded))

        def score(item: dict[str, Any]) -> tuple[int, str]:
            text = self._memory_text(item).casefold()
            overlap = sum(1 for token in tokens if token in text)
            if query_folded and query_folded in text:
                overlap += max(3, len(tokens))
            stamp = str(item.get("updated_at") or item.get("created_at") or "")
            return overlap, stamp

        ranked = sorted(items, key=score, reverse=True)
        matching = [item for item in ranked if score(item)[0] > 0]
        return (matching or ranked)[: self._config.top_k]

    def _build_outbox_event(self, request: MemoryCommitRequest) -> dict[str, Any] | None:
        inbound = self._stringify_content(request.inbound_content)
        if not inbound:
            return None
        outbound = self._stringify_content(request.outbound_content)
        messages = [{"role": "user", "content": inbound}]
        if outbound:
            messages.append({"role": "assistant", "content": outbound})

        scope = request.scope
        turn_id = uuid.uuid4().hex
        metadata: dict[str, Any] = {
            "schema_version": _STATE_VERSION,
            "memory_namespace": self._namespace,
            "source_agent": self._config.agent_id.strip() or "hahobot",
            "persona": scope.persona,
            "language": scope.language,
            "hahobot_turn_id": turn_id,
        }
        if self._config.project_id.strip():
            metadata["project_id"] = self._config.project_id.strip()
        if self._config.device_id.strip():
            metadata["device_id"] = self._config.device_id.strip()

        return {
            "id": turn_id,
            "created_at": _utc_now(),
            "attempts": 0,
            "next_attempt_at": 0.0,
            "messages": messages,
            "metadata": metadata,
        }

    async def _drain_outbox(self, *, force: bool = False) -> None:
        if self._retired or self._closed:
            return
        token = ""
        claim_task: asyncio.Task[tuple[str, list[dict[str, Any]]]] | None = None
        try:
            while True:
                claim_task = asyncio.create_task(
                    asyncio.to_thread(
                        self._state.claim_due,
                        force=force,
                        limit=_DRAIN_BATCH_SIZE,
                    )
                )
                token, events = await asyncio.shield(claim_task)
                if not events:
                    await self._arm_retry()
                    return

                succeeded: set[str] = set()
                failed: dict[str, tuple[int, float]] = {}
                for event in events:
                    event_id = str(event["id"])
                    try:
                        await self._send_event(event)
                        succeeded.add(event_id)
                    except Exception as exc:
                        attempts = int(event.get("attempts", 0) or 0) + 1
                        delay = min(300.0, float(2 ** min(attempts, 8)))
                        failed[event_id] = (attempts, time.time() + delay)
                        logger.warning("Mem0 shared-memory write queued for retry: {}", exc)
                await asyncio.to_thread(
                    self._state.finish_claim,
                    token,
                    succeeded=succeeded,
                    failed=failed,
                )
                token = ""
                if not self._retire_after_drain or failed:
                    await self._arm_retry()
                    return
        except asyncio.CancelledError:
            if not token and claim_task is not None:
                try:
                    token, _ = await claim_task
                except Exception:
                    token = ""
            if token:
                await asyncio.to_thread(self._state.release_claim, token)
            raise
        except Exception:
            if token:
                await asyncio.to_thread(self._state.release_claim, token)
            logger.exception("Mem0 shared-memory outbox drain failed")

    async def _arm_retry(self) -> None:
        if self._retired or self._closed or self._retire_after_drain:
            return
        delay = await asyncio.to_thread(self._state.next_retry_delay)
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        if delay is None:
            return
        loop = asyncio.get_running_loop()
        self._retry_handle = loop.call_later(max(0.05, delay), self._retry_wakeup)

    def _retry_wakeup(self) -> None:
        self._retry_handle = None
        if not self._retired and not self._closed:
            self._schedule_outbox_drain()

    async def _send_event(self, event: dict[str, Any]) -> None:
        metadata = event.get("metadata") or {}
        messages: list[dict[str, Any]] = []
        for raw_message in event.get("messages") or []:
            if not isinstance(raw_message, dict):
                continue
            content = self._stringify_content(raw_message.get("content"))
            if content:
                messages.append({**raw_message, "content": content})
        if not messages:
            return
        await self._request(
            "POST",
            "/memories",
            json={
                "messages": messages,
                "user_id": self._config.user_id.strip(),
                "agent_id": str(metadata.get("source_agent") or "hahobot"),
                "infer": True,
                "metadata": metadata,
            },
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["X-API-Key"] = self._config.api_key
        url = f"{self._config.base_url.rstrip('/')}/{path.lstrip('/')}"
        async with httpx.AsyncClient(
            headers=headers,
            timeout=self._config.timeout_seconds,
            transport=self._transport,
        ) as client:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}

    def _normalize_results(self, raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, dict):
            if "results" in raw:
                raw = raw["results"]
            elif "memories" in raw:
                raw = raw["memories"]
            else:
                raise ValueError("Mem0 response is missing a results list")
        if not isinstance(raw, list):
            raise ValueError("Mem0 response results must be a list")

        results: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            memory = self._memory_text(item)
            if not memory:
                continue
            memory_id = str(item.get("id") or hashlib.sha256(memory.encode()).hexdigest())
            results.append(
                {
                    "id": memory_id,
                    "memory": memory,
                    "created_at": str(item.get("created_at") or ""),
                    "updated_at": str(item.get("updated_at") or ""),
                }
            )
        return results

    def _memory_text(self, item: dict[str, Any]) -> str:
        for key in ("memory", "text", "content"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                cleaned = self._sanitize_text(value)
                return " ".join(cleaned.split()) if cleaned else ""
        return ""

    def _format_context(self, memories: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        total = 0
        seen: set[str] = set()
        for item in memories:
            memory = self._memory_text(item)
            if not memory or memory in seen:
                continue
            seen.add(memory)
            if len(memory) > _MAX_MEMORY_ITEM_CHARS:
                memory = memory[: _MAX_MEMORY_ITEM_CHARS - 3].rstrip() + "..."
            line = f"- {memory}"
            if (
                self._config.max_context_chars
                and total + len(line) + 1 > self._config.max_context_chars
            ):
                break
            lines.append(line)
            total += len(line) + 1
        return "\n".join(lines)

    def _sanitize_text(self, text: str) -> str:
        if self._namespace == "global":
            text = strip_persona_private_text(text)
        return strip_private_text(text).strip()

    def _stringify_content(self, content: Any) -> str:
        if isinstance(content, str):
            return self._sanitize_text(content)
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = self._sanitize_text(str(block.get("text") or ""))
                if text:
                    parts.append(text)
            elif isinstance(block, str):
                text = self._sanitize_text(block)
                if text:
                    parts.append(text)
        return "\n".join(parts)


class LayeredMem0SharedMemoryBackend(UserMemoryBackend):
    """Combine one Hermes-compatible public namespace with persona-private namespaces."""

    def __init__(
        self,
        config: SharedMemoryConfig,
        *,
        state_root: Path,
        persona_names: Callable[[], list[str]],
        schedule_background: Callable[[Awaitable[None]], asyncio.Task[Any]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._state_root = state_root
        self._persona_names = persona_names
        self._schedule_background = schedule_background
        self._transport = transport
        self._global = Mem0SharedMemoryBackend(
            config,
            state_root=state_root,
            schedule_background=schedule_background,
            transport=transport,
            namespace="global",
            write_mode=config.global_write_mode,
        )
        self._persona_backends: dict[str, Mem0SharedMemoryBackend] = {}
        self._started = False
        self._retired = False
        self._closed = False

    @property
    def global_backend(self) -> Mem0SharedMemoryBackend:
        """Expose the public namespace backend for diagnostics."""
        return self._global

    @property
    def persona_backends(self) -> dict[str, Mem0SharedMemoryBackend]:
        """Expose instantiated private namespace backends for diagnostics."""
        return dict(self._persona_backends)

    def persona_user_id(self, persona: str | None) -> str:
        """Return the stable Mem0 user id for one Hahobot persona."""
        normalized = normalize_persona_name(persona) or DEFAULT_PERSONA
        prefix = self._config.persona_user_id_prefix.strip().rstrip(":")
        if not prefix:
            prefix = f"{self._config.user_id.strip()}::hahobot-persona"
        private_user_id = f"{prefix}::{normalized.casefold()}"
        if private_user_id.casefold() == self._config.user_id.strip().casefold():
            raise ValueError(
                "memory.shared persona namespace collides with the public userId for "
                f"persona {normalized!r}; choose a different personaUserIdPrefix"
            )
        return private_user_id

    def _persona_backend(self, persona: str | None) -> Mem0SharedMemoryBackend:
        normalized = normalize_persona_name(persona) or DEFAULT_PERSONA
        key = normalized.casefold()
        backend = self._persona_backends.get(key)
        if backend is not None:
            return backend
        private_config = self._config.model_copy(
            update={"user_id": self.persona_user_id(normalized)}
        )
        backend = Mem0SharedMemoryBackend(
            private_config,
            state_root=self._state_root,
            schedule_background=self._schedule_background,
            transport=self._transport,
            namespace="persona",
            write_mode="full",
        )
        self._persona_backends[key] = backend
        return backend

    async def _started_persona_backend(self, persona: str | None) -> Mem0SharedMemoryBackend:
        backend = self._persona_backend(persona)
        if self._started:
            await backend.start()
        return backend

    async def start(self) -> None:
        """Recover public and every currently installed persona outbox."""
        if self._started or self._retired or self._closed:
            return
        try:
            personas = self._persona_names()
        except Exception:
            logger.exception("Failed to enumerate personas for shared-memory recovery")
            personas = [DEFAULT_PERSONA]
        persona_backends: list[Mem0SharedMemoryBackend] = []
        for persona in personas:
            try:
                persona_backends.append(self._persona_backend(persona))
            except ValueError as exc:
                logger.error("Skipping unsafe Mem0 persona namespace: {}", exc)
        self._started = True
        await asyncio.gather(
            self._global.start(),
            *(backend.start() for backend in persona_backends),
            return_exceptions=True,
        )

    def retire(self) -> None:
        """Immediately stop every namespace owned by this router generation."""
        if self._retired:
            return
        self._retired = True
        self._global.retire()
        for backend in self._persona_backends.values():
            backend.retire()

    def retire_after_pending_work(self) -> None:
        """Give final public/private events one attempt before retiring."""
        if self._retired:
            return
        self._retired = True
        self._global.retire_after_pending_work()
        for backend in self._persona_backends.values():
            backend.retire_after_pending_work()

    async def close(self) -> None:
        """Close all namespace workers idempotently."""
        if self._closed:
            return
        self.retire()
        await asyncio.gather(
            self._global.close(),
            *(backend.close() for backend in self._persona_backends.values()),
            return_exceptions=True,
        )
        self._closed = True

    async def resolve_context(self, scope: MemoryScope) -> ResolvedMemoryContext:
        """Recall the public namespace plus the current persona's private namespace."""
        private = await self._started_persona_backend(scope.persona)
        public_result, private_result = await asyncio.gather(
            self._global.resolve_context(scope),
            private.resolve_context(scope),
            return_exceptions=True,
        )
        sections: list[str] = []
        sources: list[str] = []
        for title, result in (
            ("Public shared facts", public_result),
            (f"Private facts for persona {scope.persona}", private_result),
        ):
            if isinstance(result, BaseException):
                logger.warning("Mem0 {} recall failed: {}", title, result)
                continue
            sources.append(result.source)
            if result.block.strip():
                sections.append(f"[{title}]\n{result.block.strip()}")
        block = "\n\n".join(sections)
        limit = self._config.max_context_chars
        if limit and len(block) > limit:
            block = block[: max(0, limit - 3)].rstrip() + "..."
        return ResolvedMemoryContext(
            block=block,
            source="+".join(sources) or "mem0-layered",
        )

    async def commit_turn(self, request: MemoryCommitRequest) -> None:
        """Write the full turn privately and route configured user content publicly."""
        private = await self._started_persona_backend(request.scope.persona)
        results = await asyncio.gather(
            private.commit_turn(request),
            self._global.commit_turn(request),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("Layered Mem0 commit failed: {}", result)

    async def flush_session(self, scope: MemoryScope) -> None:
        """Flush public and current-persona writes before a scope transition."""
        private = await self._started_persona_backend(scope.persona)
        await asyncio.gather(
            self._global.flush_session(scope),
            private.flush_session(scope),
            return_exceptions=True,
        )


def build_mem0_shared_backend(
    config: SharedMemoryConfig,
    *,
    state_root: Path,
    persona_names: Callable[[], list[str]],
    schedule_background: Callable[[Awaitable[None]], asyncio.Task[Any]] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> UserMemoryBackend:
    """Build the single public namespace or the optional layered persona mode."""
    if config.persona_enabled:
        return LayeredMem0SharedMemoryBackend(
            config,
            state_root=state_root,
            persona_names=persona_names,
            schedule_background=schedule_background,
            transport=transport,
        )
    return Mem0SharedMemoryBackend(
        config,
        state_root=state_root,
        schedule_background=schedule_background,
        transport=transport,
    )
