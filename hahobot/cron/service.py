"""Cron service for scheduling agent tasks."""

import asyncio
import copy
import errno
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypeVar

from loguru import logger

from hahobot.cron.types import (
    CronJob,
    CronJobState,
    CronPayload,
    CronRunRecord,
    CronSchedule,
    CronStore,
)
from hahobot.utils.helpers import _write_text_atomic

_T = TypeVar("_T")
_StoreSignature = tuple[int, int, int]
_EMPTY_STORE_SIGNATURE: _StoreSignature = (0, 0, 0)
_STORE_LOCK_TIMEOUT_SECONDS = 10.0
_JOB_CLAIM_TTL_MS = 6 * 60 * 60 * 1000
_STORE_THREAD_LOCKS: dict[str, threading.RLock] = {}
_STORE_THREAD_LOCKS_GUARD = threading.Lock()
_STORE_IO_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hahobot-cron-store")
_STORE_PATH_OVERRIDE: ContextVar[Path | None] = ContextVar("cron_store_path_override", default=None)


class _StoreOperationCancelledError(RuntimeError):
    """Internal signal used to abort an uncommitted worker transaction."""


class _StoreOperationState:
    """Coordinate cancellation with the store's atomic replace boundary."""

    def __init__(self) -> None:
        self._commit_lock = threading.Lock()
        self.cancel_requested = threading.Event()
        self.committed = False

    def request_cancel(self) -> None:
        """Record cancellation without contending with pre-commit checks."""
        self.cancel_requested.set()

    def check_cancelled(self) -> None:
        if self.cancel_requested.is_set() and not self.committed:
            raise _StoreOperationCancelledError("cron store operation cancelled before commit")

    def replace(self, temporary: Path, target: Path) -> None:
        """Linearize cancellation against the atomic filesystem replace."""
        with self._commit_lock:
            if self.cancel_requested.is_set():
                raise _StoreOperationCancelledError("cron store operation cancelled before commit")
            temporary.replace(target)
            self.committed = True


_STORE_OPERATION_STATE: ContextVar[_StoreOperationState | None] = ContextVar(
    "cron_store_operation_state", default=None
)


def _check_store_operation_cancelled() -> None:
    state = _STORE_OPERATION_STATE.get()
    if state is not None:
        state.check_cancelled()


def _replace_store_file(temporary: Path, target: Path) -> None:
    state = _STORE_OPERATION_STATE.get()
    if state is None:
        temporary.replace(target)
        return
    state.replace(temporary, target)


async def _await_uninterruptibly(future: asyncio.Future[_T]) -> _T:
    """Wait for a worker/result boundary even if cancellation is requested again."""
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            continue
    return future.result()


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _STORE_THREAD_LOCKS_GUARD:
        return _STORE_THREAD_LOCKS.setdefault(key, threading.RLock())


def _in_running_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@contextmanager
def _exclusive_file_lock(path: Path, *, wait: bool = True) -> Iterator[None]:
    """Hold a portable non-destructive lock on a companion file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + (_STORE_LOCK_TIMEOUT_SECONDS if wait else 0.0)
    with open(path, "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()

            while True:
                _check_store_operation_cancelled()
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if (
                        exc.errno not in {errno.EACCES, errno.EAGAIN}
                        or time.monotonic() >= deadline
                    ):
                        raise TimeoutError(f"timed out locking cron store {path}") from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        while True:
            _check_store_operation_cancelled()
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out locking cron store {path}") from exc
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _store_transaction_lock(path: Path) -> Iterator[None]:
    """Acquire thread + process locks without blocking a running event loop."""
    thread_lock = _thread_lock_for(path)
    wait = not _in_running_event_loop()
    if wait:
        deadline = time.monotonic() + _STORE_LOCK_TIMEOUT_SECONDS
        acquired = False
        while not acquired:
            _check_store_operation_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            acquired = thread_lock.acquire(timeout=min(0.01, remaining))
    else:
        acquired = thread_lock.acquire(blocking=False)
    if not acquired:
        raise TimeoutError(f"timed out locking cron store {path}")
    try:
        _check_store_operation_cancelled()
        with _exclusive_file_lock(path, wait=wait):
            _check_store_operation_cancelled()
            yield
    finally:
        thread_lock.release()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _store_signature(stat_result: os.stat_result) -> _StoreSignature:
    return (stat_result.st_mtime_ns, stat_result.st_ino, stat_result.st_size)


def _claim_is_active(job: CronJob, now_ms: int) -> bool:
    claimed_at = job.state.running_at_ms
    return bool(
        job.state.running_token
        and claimed_at is not None
        and now_ms - claimed_at < _JOB_CLAIM_TTL_MS
    )


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    """Compute next run time in ms."""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        # Next interval from now.  This is intentionally based on now_ms
        # rather than last_run_at_ms so that long-running jobs don't cause
        # a burst of catch-up runs.  Callers that need cadence-based
        # scheduling should pass last_run_at_ms + every_ms as now_ms.
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            from croniter import croniter

            # Use caller-provided reference time for deterministic scheduling
            base_time = now_ms / 1000
            tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base_dt = datetime.fromtimestamp(base_time, tz=tz)
            cron = croniter(schedule.expr, base_dt)
            next_dt = cron.get_next(datetime)
            return int(next_dt.timestamp() * 1000)
        except Exception:
            return None

    return None


def _validate_schedule_for_add(schedule: CronSchedule) -> None:
    """Validate schedule fields that would otherwise create non-runnable jobs."""
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")

    if schedule.kind == "cron" and schedule.tz:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(schedule.tz)
        except Exception:
            raise ValueError(f"unknown timezone '{schedule.tz}'") from None


class CronService:
    """Service for managing and executing scheduled jobs."""

    _MAX_RUN_HISTORY = 20

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None,
        max_sleep_ms: int = 300_000,  # 5 minutes
    ):
        self.store_path = store_path
        self.on_job = on_job
        self._store: CronStore | None = None
        self._last_signature = _EMPTY_STORE_SIGNATURE
        self._timer_task: asyncio.Task | None = None
        self._timer_execution_tasks: set[asyncio.Task] = set()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._cache_lock = threading.RLock()
        self._rebind_lock = threading.Lock()
        self._async_rebind_lock = asyncio.Lock()
        self._rebind_in_progress = False
        self._running = False
        self.max_sleep_ms = max_sleep_ms

    def _effective_store_path(self) -> Path:
        return _STORE_PATH_OVERRIDE.get() or self.store_path

    @property
    def _lock_path(self) -> Path:
        store_path = self._effective_store_path()
        return store_path.with_suffix(store_path.suffix + ".lock")

    @staticmethod
    def _read_store_path(
        store_path: Path,
        *,
        strict: bool = False,
    ) -> tuple[CronStore, _StoreSignature]:
        if not store_path.exists():
            return CronStore(), _EMPTY_STORE_SIGNATURE
        try:
            with open(store_path, encoding="utf-8") as handle:
                data = json.load(handle)
                loaded_signature = _store_signature(os.fstat(handle.fileno()))
            if not isinstance(data, dict):
                raise TypeError("cron store root must be an object")
            raw_jobs = data.get("jobs", [])
            if not isinstance(raw_jobs, list):
                raise TypeError("cron store jobs must be an array")
            jobs = [CronJob.from_store_dict(job) for job in raw_jobs if isinstance(job, dict)]
            return CronStore(jobs=jobs), loaded_signature
        except Exception as exc:
            logger.warning("Failed to load cron store {}: {}", store_path, exc)
            if strict:
                raise
            return CronStore(), _EMPTY_STORE_SIGNATURE

    def _load_store(self) -> CronStore:
        """Load jobs from disk. Reloads automatically if file was modified externally."""
        store_path = self._effective_store_path()
        with self._cache_lock:
            # A pinned operation for an old workspace must never replace the
            # active workspace's in-memory scheduler cache.
            if store_path != self.store_path:
                return self._read_store_path(store_path)[0]

            if self._store is not None and store_path.exists():
                signature = _store_signature(store_path.stat())
                if signature != self._last_signature:
                    logger.info("Cron: jobs.json modified externally, reloading")
                    self._store = None
            if self._store is None:
                self._store, self._last_signature = self._read_store_path(store_path)
            return self._store

    def _save_store_unlocked(
        self,
        store: CronStore | None = None,
        store_path: Path | None = None,
    ) -> _StoreSignature:
        """Atomically save one store while its companion lock is held."""
        active_store = store if store is not None else self._store
        target = store_path or self._effective_store_path()
        if active_store is None:
            return _EMPTY_STORE_SIGNATURE

        target.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": active_store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "kind": j.payload.kind,
                        "message": j.payload.message,
                        "deliver": j.payload.deliver,
                        "channel": j.payload.channel,
                        "to": j.payload.to,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                        "runningToken": j.state.running_token,
                        "runningAtMs": j.state.running_at_ms,
                        "runHistory": [
                            {
                                "runAtMs": r.run_at_ms,
                                "status": r.status,
                                "durationMs": r.duration_ms,
                                "error": r.error,
                            }
                            for r in j.state.run_history
                        ],
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in active_store.jobs
            ],
        }

        _write_text_atomic(
            target,
            json.dumps(data, indent=2, ensure_ascii=False),
            replace_file=_replace_store_file,
        )
        return _store_signature(target.stat())

    def _save_store(self) -> None:
        """Atomically save jobs under the cross-process companion lock."""
        store_path = self._effective_store_path()
        lock_path = store_path.with_suffix(store_path.suffix + ".lock")
        with self._cache_lock, _store_transaction_lock(lock_path):
            signature = self._save_store_unlocked(store_path=store_path)
            if store_path == self.store_path:
                self._last_signature = signature

    def _mutate_store(self, mutator: Callable[[CronStore], _T]) -> _T:
        """Run one complete read-modify-write transaction under a file lock."""
        store_path = self._effective_store_path()
        lock_path = store_path.with_suffix(store_path.suffix + ".lock")
        with _store_transaction_lock(lock_path):
            # Always re-read after acquiring the lock. Another process may have
            # committed between our cached read and this mutation.
            # A read-only caller may degrade to an empty view of a corrupt
            # store. A mutation must fail instead of overwriting evidence with
            # a newly serialized empty store.
            store, _ = self._read_store_path(store_path, strict=True)
            _check_store_operation_cancelled()
            result = mutator(store)
            _check_store_operation_cancelled()
            signature = self._save_store_unlocked(store=store, store_path=store_path)

        with self._cache_lock:
            if store_path == self.store_path:
                self._store = store
                self._last_signature = signature
        return result

    async def start(self) -> None:
        """Start the cron service."""
        self._event_loop = asyncio.get_running_loop()
        self._running = True

        def initialize(store: CronStore) -> None:
            self._recompute_next_runs(store)

        try:
            await self.run_store_io(self._mutate_store, initialize)
        except BaseException:
            self._running = False
            raise
        self._arm_timer()
        logger.info(
            "Cron service started with {} jobs", len(self._store.jobs if self._store else [])
        )

    def stop(self) -> None:
        """Stop the cron service."""
        self._running = False

        loop = self._event_loop
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if loop is not None and loop.is_running() and current_loop is not loop:
            loop.call_soon_threadsafe(self._cancel_timer_tasks)
            return
        self._cancel_timer_tasks()

    def _cancel_timer_tasks(self) -> None:
        """Cancel the pending sleeper and any currently executing timer ticks."""
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None
        for task in tuple(self._timer_execution_tasks):
            task.cancel()

    async def run_store_io(
        self,
        operation: Callable[..., _T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """Run a store operation off-loop, pinned to the submission workspace.

        Cancellation wins before the atomic commit boundary. After that boundary,
        cancellation is delayed until the transaction has linearized so a worker
        cannot perform a hidden durable write after this coroutine returns.
        """
        loop = asyncio.get_running_loop()
        context = copy_context()
        call = partial(operation, *args, **kwargs)
        store_path = self._effective_store_path()
        operation_state = _StoreOperationState()

        def invoke() -> _T:
            def run_pinned() -> _T:
                path_token = _STORE_PATH_OVERRIDE.set(store_path)
                operation_token = _STORE_OPERATION_STATE.set(operation_state)
                try:
                    _check_store_operation_cancelled()
                    return call()
                finally:
                    _STORE_OPERATION_STATE.reset(operation_token)
                    _STORE_PATH_OVERRIDE.reset(path_token)

            return context.run(run_pinned)

        future = loop.run_in_executor(_STORE_IO_EXECUTOR, invoke)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError as cancelled:
            operation_state.request_cancel()
            try:
                # Do not return while a worker can still commit in secret.
                # If atomic replace has already won, its result wins below.
                result = await _await_uninterruptibly(future)
            except _StoreOperationCancelledError:
                raise cancelled from None
            except Exception:
                raise cancelled from None
            if operation_state.committed:
                return result
            raise cancelled

    async def rebind_store_async(self, store_path: Path) -> None:
        """Quiesce scheduler ticks, then rebind without blocking the event loop."""
        async with self._async_rebind_lock:
            self._rebind_in_progress = True
            try:
                await self._cancel_and_drain_timer_tasks()
                loop = asyncio.get_running_loop()
                context = copy_context()

                def invoke() -> None:
                    def run_unpinned() -> None:
                        token = _STORE_PATH_OVERRIDE.set(None)
                        try:
                            self.rebind_store(store_path)
                        finally:
                            _STORE_PATH_OVERRIDE.reset(token)

                    context.run(run_unpinned)

                future = loop.run_in_executor(_STORE_IO_EXECUTOR, invoke)
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    # Once submitted, rebind is a durable operator action. A
                    # successful switch wins so the caller continues applying
                    # the rest of the runtime reload against the same workspace.
                    await _await_uninterruptibly(future)
            finally:
                self._rebind_in_progress = False
                if self._running:
                    self._arm_timer()

    async def _cancel_and_drain_timer_tasks(self) -> None:
        """Stop old-workspace scheduler ticks before publishing a new store."""
        tasks = set(self._timer_execution_tasks)
        if self._timer_task is not None:
            tasks.add(self._timer_task)
            self._timer_task = None
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        drain = asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.shield(drain)
        except asyncio.CancelledError as cancelled:
            await _await_uninterruptibly(drain)
            raise cancelled

    def rebind_store(self, store_path: Path) -> None:
        """Switch the backing jobs store to a new workspace-scoped path."""
        with self._rebind_lock:
            with self._cache_lock:
                if self.store_path == store_path:
                    return

            initialized_store: CronStore | None = None
            initialized_signature = _EMPTY_STORE_SIGNATURE
            if self._running:

                def initialize(store: CronStore) -> CronStore:
                    self._recompute_next_runs(store)
                    return store

                token = _STORE_PATH_OVERRIDE.set(store_path)
                try:
                    # Prepare the target completely before publishing the new
                    # path. A read/write/lock failure leaves the old scheduler live.
                    initialized_store = self._mutate_store(initialize)
                    initialized_signature = (
                        _store_signature(store_path.stat())
                        if store_path.exists()
                        else _EMPTY_STORE_SIGNATURE
                    )
                finally:
                    _STORE_PATH_OVERRIDE.reset(token)

            with self._cache_lock:
                self.store_path = store_path
                self._store = initialized_store
                self._last_signature = initialized_signature
            if self._running:
                self._arm_timer()

    def apply_runtime_config(self, max_sleep_ms: int) -> None:
        """Apply hot-reloadable scheduler settings."""
        if max_sleep_ms == self.max_sleep_ms:
            return
        self.max_sleep_ms = max_sleep_ms
        if self._running:
            self._arm_timer()

    def _recompute_next_runs(self, store: CronStore | None = None) -> None:
        """Recompute next run times for all enabled jobs."""
        active_store = store or self._store
        if not active_store:
            return
        now = _now_ms()
        for job in active_store.jobs:
            if job.enabled:
                if _claim_is_active(job, now):
                    continue
                if job.state.running_token:
                    # Preserve the original due time when recovering a stale
                    # claim so a crashed one-shot job remains retryable.
                    job.state.running_token = None
                    job.state.running_at_ms = None
                    if job.state.next_run_at_ms is not None:
                        continue
                job.state.next_run_at_ms = _compute_next_run(job.schedule, now)

    def _get_next_wake_ms(self) -> int | None:
        """Get the earliest next run time across all jobs."""
        if not self._store:
            return None
        now = _now_ms()
        times: list[int] = []
        for job in self._store.jobs:
            if not job.enabled or job.state.next_run_at_ms is None:
                continue
            if _claim_is_active(job, now) and job.state.running_at_ms is not None:
                times.append(job.state.running_at_ms + _JOB_CLAIM_TTL_MS)
            else:
                times.append(job.state.next_run_at_ms)
        return min(times) if times else None

    def _claim_due_jobs(self, now_ms: int) -> list[CronJob]:
        """Atomically claim every currently due job for this scheduler tick."""

        def claim(store: CronStore) -> list[CronJob]:
            claimed: list[CronJob] = []
            for job in store.jobs:
                if (
                    not job.enabled
                    or job.state.next_run_at_ms is None
                    or job.state.next_run_at_ms > now_ms
                    or _claim_is_active(job, now_ms)
                ):
                    continue
                job.state.running_token = uuid.uuid4().hex
                job.state.running_at_ms = now_ms
                claimed.append(copy.deepcopy(job))
            return claimed

        return self._mutate_store(claim)

    def _claim_job(self, job_id: str, *, force: bool) -> CronJob | None:
        """Atomically claim one manually requested job."""
        now_ms = _now_ms()

        def claim(store: CronStore) -> CronJob | None:
            job = next((candidate for candidate in store.jobs if candidate.id == job_id), None)
            if job is None or (not force and not job.enabled) or _claim_is_active(job, now_ms):
                return None
            job.state.running_token = uuid.uuid4().hex
            job.state.running_at_ms = now_ms
            return copy.deepcopy(job)

        return self._mutate_store(claim)

    def _arm_timer(self, *, delay_override_ms: int | None = None) -> None:
        """Schedule the next timer tick.

        Wake periodically even when the next known job is far away or absent,
        so externally added/updated jobs are picked up without requiring a
        second scheduler process restart.
        """
        with self._cache_lock:
            if self._effective_store_path() != self.store_path:
                return
        loop = self._event_loop
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if loop is None and current_loop is not None:
            self._event_loop = current_loop
            loop = current_loop
        if loop is not None and loop.is_running() and current_loop is not loop:
            loop.call_soon_threadsafe(partial(self._arm_timer, delay_override_ms=delay_override_ms))
            return

        if self._timer_task:
            self._timer_task.cancel()

        if not self._running or self._rebind_in_progress:
            return

        if delay_override_ms is not None:
            delay_ms = max(0, delay_override_ms)
        else:
            next_wake = self._get_next_wake_ms()
            if next_wake is None:
                delay_ms = self.max_sleep_ms
            else:
                delay_ms = min(self.max_sleep_ms, max(0, next_wake - _now_ms()))
        delay_s = delay_ms / 1000

        async def tick():
            await asyncio.sleep(delay_s)
            task = asyncio.current_task()
            if self._timer_task is task:
                # From this point onward ``_timer_task`` only tracks a sleeper.
                # Runtime mutations may replace that sleeper without cancelling
                # a job whose external side effects are already in progress.
                self._timer_task = None
            if not self._running or task is None:
                return
            self._timer_execution_tasks.add(task)
            try:
                await self._on_timer()
            finally:
                self._timer_execution_tasks.discard(task)

        self._timer_task = asyncio.create_task(tick())

    async def _on_timer(self) -> None:
        """Handle timer tick - run due jobs."""
        store_token = _STORE_PATH_OVERRIDE.set(self._effective_store_path())
        pending_claims: list[CronJob] = []
        retry_delay_ms: int | None = None
        try:
            claim_task = asyncio.create_task(self.run_store_io(self._claim_due_jobs, _now_ms()))
            try:
                pending_claims = await asyncio.shield(claim_task)
            except asyncio.CancelledError:
                try:
                    pending_claims = await _await_uninterruptibly(claim_task)
                except _StoreOperationCancelledError:
                    pending_claims = []
                if pending_claims:
                    await self.run_store_io(self._release_claims, pending_claims)
                    pending_claims = []
                raise

            for job in list(pending_claims):
                delete_after_run = await self._execute_job(job)
                # Once the callback returned, do not release this claim on a
                # commit failure: an immediate retry could duplicate a side effect.
                pending_claims.remove(job)
                commit_task = asyncio.create_task(
                    self.run_store_io(
                        self._persist_execution_outcomes,
                        [(job, delete_after_run)],
                    )
                )
                try:
                    await asyncio.shield(commit_task)
                except asyncio.CancelledError:
                    await _await_uninterruptibly(commit_task)
                    raise
        except asyncio.CancelledError:
            raise
        except Exception:
            retry_delay_ms = 250
            logger.exception("Cron timer tick failed; retrying")
        finally:
            if pending_claims:
                try:
                    release_task = asyncio.create_task(
                        self.run_store_io(self._release_claims, pending_claims)
                    )
                    try:
                        await asyncio.shield(release_task)
                    except asyncio.CancelledError:
                        await _await_uninterruptibly(release_task)
                        raise
                except Exception:
                    logger.exception("Cron: failed to release interrupted job claims")
            if self._running:
                self._arm_timer(delay_override_ms=retry_delay_ms)
            _STORE_PATH_OVERRIDE.reset(store_token)

    async def _execute_job(self, job: CronJob) -> bool:
        """Execute one detached job and return whether it should be deleted."""
        start_ms = _now_ms()
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)

        try:
            if self.on_job:
                await self.on_job(job)

            job.state.last_status = "ok"
            job.state.last_error = None
            logger.info("Cron: job '{}' completed", job.name)

        except Exception as e:
            job.state.last_status = "error"
            job.state.last_error = str(e)
            logger.error("Cron: job '{}' failed: {}", job.name, e)

        end_ms = _now_ms()
        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = end_ms

        job.state.run_history.append(
            CronRunRecord(
                run_at_ms=start_ms,
                status=job.state.last_status,
                duration_ms=end_ms - start_ms,
                error=job.state.last_error,
            )
        )
        job.state.run_history = job.state.run_history[-self._MAX_RUN_HISTORY :]

        # Handle one-shot jobs
        if job.schedule.kind == "at":
            if job.delete_after_run:
                return True
            job.enabled = False
            job.state.next_run_at_ms = None
        else:
            # Compute next run.  For "every" schedules, base the next run on
            # last_run_at_ms to prevent cumulative drift from execution time.
            base_ms = _now_ms()
            if job.schedule.kind == "every" and job.state.last_run_at_ms:
                candidate = job.state.last_run_at_ms + (job.schedule.every_ms or 0)
                # Don't schedule in the past — clamp to now.
                base_ms = max(candidate, base_ms)
                job.state.next_run_at_ms = base_ms
            else:
                job.state.next_run_at_ms = _compute_next_run(job.schedule, base_ms)
        return False

    def _persist_execution_outcomes(self, outcomes: list[tuple[CronJob, bool]]) -> None:
        """Merge detached execution state into the latest cross-process store."""

        def merge(store: CronStore) -> None:
            for executed, delete_after_run in outcomes:
                current = next((job for job in store.jobs if job.id == executed.id), None)
                if current is None:
                    # Respect an operator deletion that happened while the job ran.
                    continue
                if (
                    not executed.state.running_token
                    or current.state.running_token != executed.state.running_token
                ):
                    # A stale execution must not clear or overwrite a newer claim.
                    logger.warning("Cron: ignored stale execution outcome for job {}", executed.id)
                    continue
                if delete_after_run:
                    store.jobs = [job for job in store.jobs if job.id != executed.id]
                    continue

                current.state.last_run_at_ms = executed.state.last_run_at_ms
                current.state.last_status = executed.state.last_status
                current.state.last_error = executed.state.last_error
                if executed.state.run_history:
                    current.state.run_history.append(copy.deepcopy(executed.state.run_history[-1]))
                    current.state.run_history = current.state.run_history[-self._MAX_RUN_HISTORY :]
                current.state.running_token = None
                current.state.running_at_ms = None
                current.updated_at_ms = executed.updated_at_ms

                if current.schedule.kind == "at":
                    current.enabled = False
                    current.state.next_run_at_ms = None
                elif current.enabled:
                    base_ms = _now_ms()
                    if current.schedule.kind == "every" and current.state.last_run_at_ms:
                        candidate = current.state.last_run_at_ms + (current.schedule.every_ms or 0)
                        base_ms = max(candidate, base_ms)
                        current.state.next_run_at_ms = base_ms
                    else:
                        current.state.next_run_at_ms = _compute_next_run(current.schedule, base_ms)

        self._mutate_store(merge)

    def _release_claims(self, jobs: list[CronJob]) -> None:
        """Release matching claims after cancellation or a failed outcome commit."""
        tokens = {
            job.id: job.state.running_token for job in jobs if job.state.running_token is not None
        }

        def release(store: CronStore) -> None:
            for current in store.jobs:
                if tokens.get(current.id) == current.state.running_token:
                    current.state.running_token = None
                    current.state.running_at_ms = None

        self._mutate_store(release)

    # ========== Public API ==========

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """List all jobs."""
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float("inf"))

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        """Add a new job."""
        _validate_schedule_for_add(schedule)
        now = _now_ms()

        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                kind="agent_turn",
                message=message,
                deliver=deliver,
                channel=channel,
                to=to,
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )

        self._mutate_store(lambda store: store.jobs.append(job))
        self._arm_timer()

        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    def register_system_job(self, job: CronJob) -> CronJob:
        """Register an internal system job (idempotent on restart)."""
        now = _now_ms()
        job.state = CronJobState(next_run_at_ms=_compute_next_run(job.schedule, now))
        job.created_at_ms = now
        job.updated_at_ms = now

        def register(store: CronStore) -> None:
            store.jobs = [existing for existing in store.jobs if existing.id != job.id]
            store.jobs.append(job)

        self._mutate_store(register)
        self._arm_timer()
        logger.info("Cron: registered system job '{}' ({})", job.name, job.id)
        return job

    def remove_job(self, job_id: str) -> Literal["removed", "protected", "not_found"]:
        """Remove a job by ID, unless it is a protected system job."""

        def remove(store: CronStore) -> Literal["removed", "protected", "not_found"]:
            job = next((candidate for candidate in store.jobs if candidate.id == job_id), None)
            if job is None:
                return "not_found"
            if job.payload.kind == "system_event":
                return "protected"
            store.jobs = [candidate for candidate in store.jobs if candidate.id != job_id]
            return "removed"

        result = self._mutate_store(remove)
        if result == "removed":
            self._arm_timer()
            logger.info("Cron: removed job {}", job_id)
        elif result == "protected":
            logger.info("Cron: refused to remove protected system job {}", job_id)
        return result

    def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
        """Enable or disable a job."""

        def update(store: CronStore) -> CronJob | None:
            for job in store.jobs:
                if job.id == job_id:
                    job.enabled = enabled
                    job.updated_at_ms = _now_ms()
                    if enabled:
                        job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())
                    else:
                        job.state.next_run_at_ms = None
                    return job
            return None

        job = self._mutate_store(update)
        if job is not None:
            self._arm_timer()
        return job

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """Manually run a job."""
        store_token = _STORE_PATH_OVERRIDE.set(self._effective_store_path())
        detached: CronJob | None = None
        execution_completed = False
        was_running = self._running
        try:
            claim_task = asyncio.create_task(
                self.run_store_io(self._claim_job, job_id, force=force)
            )
            try:
                detached = await asyncio.shield(claim_task)
            except asyncio.CancelledError:
                try:
                    detached = await _await_uninterruptibly(claim_task)
                except _StoreOperationCancelledError:
                    detached = None
                if detached is not None:
                    await self.run_store_io(self._release_claims, [detached])
                raise
            if detached is None:
                return False

            delete_after_run = await self._execute_job(detached)
            execution_completed = True
            commit_task = asyncio.create_task(
                self.run_store_io(
                    self._persist_execution_outcomes,
                    [(detached, delete_after_run)],
                )
            )
            try:
                await asyncio.shield(commit_task)
            except asyncio.CancelledError:
                await _await_uninterruptibly(commit_task)
                raise
            return True
        except BaseException:
            if detached is not None and not execution_completed:
                try:
                    await self.run_store_io(self._release_claims, [detached])
                except Exception:
                    logger.exception("Cron: failed to release interrupted manual-run claim")
            raise
        finally:
            if was_running and self._running:
                self._arm_timer()
            _STORE_PATH_OVERRIDE.reset(store_token)

    def get_job(self, job_id: str) -> CronJob | None:
        """Get a job by ID."""
        store = self._load_store()
        return next((j for j in store.jobs if j.id == job_id), None)

    def status(self) -> dict:
        """Get service status."""
        store = self._load_store()
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
