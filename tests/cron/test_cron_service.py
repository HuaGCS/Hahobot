import asyncio
import json
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest

import hahobot.cron.service as cron_service_module
from hahobot.cron.service import CronService, _thread_lock_for
from hahobot.cron.types import CronJob, CronJobState, CronPayload, CronRunRecord, CronSchedule


def _add_jobs_in_process(store_path: str, prefix: str, count: int) -> None:
    service = CronService(Path(store_path))
    for index in range(count):
        service.add_job(
            name=f"{prefix}-{index}",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="hello",
        )


def test_add_job_rejects_unknown_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(ValueError, match="unknown timezone 'America/Vancovuer'"):
        service.add_job(
            name="tz typo",
            schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancovuer"),
            message="hello",
        )

    assert service.list_jobs(include_disabled=True) == []


def test_add_job_accepts_valid_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    job = service.add_job(
        name="tz ok",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancouver"),
        message="hello",
    )

    assert job.schedule.tz == "America/Vancouver"
    assert job.state.next_run_at_ms is not None


@pytest.mark.asyncio
async def test_execute_job_records_run_history(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    job = service.add_job(
        name="hist",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    await service.run_job(job.id)

    loaded = service.get_job(job.id)
    assert loaded is not None
    assert len(loaded.state.run_history) == 1
    rec = loaded.state.run_history[0]
    assert rec.status == "ok"
    assert rec.duration_ms >= 0
    assert rec.error is None


@pytest.mark.asyncio
async def test_run_history_records_errors(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"

    async def fail(_):
        raise RuntimeError("boom")

    service = CronService(store_path, on_job=fail)
    job = service.add_job(
        name="fail",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    await service.run_job(job.id)

    loaded = service.get_job(job.id)
    assert len(loaded.state.run_history) == 1
    assert loaded.state.run_history[0].status == "error"
    assert loaded.state.run_history[0].error == "boom"


@pytest.mark.asyncio
async def test_run_history_trimmed_to_max(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    job = service.add_job(
        name="trim",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    for _ in range(25):
        await service.run_job(job.id)

    loaded = service.get_job(job.id)
    assert len(loaded.state.run_history) == CronService._MAX_RUN_HISTORY


@pytest.mark.asyncio
async def test_run_history_persisted_to_disk(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    job = service.add_job(
        name="persist",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    await service.run_job(job.id)

    raw = json.loads(store_path.read_text())
    history = raw["jobs"][0]["state"]["runHistory"]
    assert len(history) == 1
    assert history[0]["status"] == "ok"
    assert "runAtMs" in history[0]
    assert "durationMs" in history[0]

    fresh = CronService(store_path)
    loaded = fresh.get_job(job.id)
    assert len(loaded.state.run_history) == 1
    assert loaded.state.run_history[0].status == "ok"


@pytest.mark.asyncio
async def test_running_service_honors_external_disable(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    called: list[str] = []

    async def on_job(job) -> None:
        called.append(job.id)

    service = CronService(store_path, on_job=on_job)
    job = service.add_job(
        name="external-disable",
        schedule=CronSchedule(kind="every", every_ms=200),
        message="hello",
    )
    await service.start()
    try:
        # Wait slightly to ensure file mtime is definitively different
        await asyncio.sleep(0.05)
        external = CronService(store_path)
        updated = external.enable_job(job.id, enabled=False)
        assert updated is not None
        assert updated.enabled is False

        await asyncio.sleep(0.35)
        assert called == []
    finally:
        service.stop()


def test_remove_job_refuses_system_jobs(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    service.register_system_job(
        CronJob(
            id="dream",
            name="dream",
            schedule=CronSchedule(kind="cron", expr="0 */2 * * *", tz="UTC"),
            payload=CronPayload(kind="system_event"),
        )
    )

    result = service.remove_job("dream")

    assert result == "protected"
    assert service.get_job("dream") is not None


def test_reload_jobs(tmp_path):
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    service.add_job(
        name="hist",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )

    assert len(service.list_jobs()) == 1

    service2 = CronService(tmp_path / "cron" / "jobs.json")
    service2.add_job(
        name="hist2",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello2",
    )
    assert len(service.list_jobs()) == 2


def test_mutation_does_not_overwrite_malformed_store(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    store_path.parent.mkdir(parents=True)
    malformed = b'{"jobs": ['
    store_path.write_bytes(malformed)
    service = CronService(store_path)

    assert service.list_jobs(include_disabled=True) == []
    with pytest.raises(json.JSONDecodeError):
        service.add_job(
            name="must-not-overwrite",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="hello",
        )

    assert store_path.read_bytes() == malformed


@pytest.mark.asyncio
async def test_start_failure_restores_stopped_state(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text('{"jobs": [', encoding="utf-8")
    service = CronService(store_path)

    with pytest.raises(json.JSONDecodeError):
        await service.start()

    assert service.status()["enabled"] is False
    assert service._timer_task is None


def test_concurrent_store_instances_preserve_every_added_job(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"

    def add_many(prefix: str) -> None:
        service = CronService(store_path)
        for index in range(20):
            service.add_job(
                name=f"{prefix}-{index}",
                schedule=CronSchedule(kind="every", every_ms=60_000),
                message="hello",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(add_many, "a")
        second = pool.submit(add_many, "b")
        first.result(timeout=30)
        second.result(timeout=30)

    names = {job.name for job in CronService(store_path).list_jobs(include_disabled=True)}
    assert names == {f"{prefix}-{index}" for prefix in ("a", "b") for index in range(20)}
    assert store_path.with_suffix(".json.lock").exists()


def test_concurrent_processes_preserve_every_added_job(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"

    with ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as pool:
        first = pool.submit(_add_jobs_in_process, str(store_path), "p1", 10)
        second = pool.submit(_add_jobs_in_process, str(store_path), "p2", 10)
        first.result(timeout=30)
        second.result(timeout=30)

    names = {job.name for job in CronService(store_path).list_jobs(include_disabled=True)}
    assert names == {f"{prefix}-{index}" for prefix in ("p1", "p2") for index in range(10)}


@pytest.mark.asyncio
async def test_concurrent_manual_runs_claim_job_once(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def on_job(job: CronJob) -> None:
        calls.append(job.id)
        started.set()
        await release.wait()

    first_service = CronService(store_path, on_job=on_job)
    second_service = CronService(store_path, on_job=on_job)
    job = first_service.add_job(
        name="claimed-once",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )

    first_run = asyncio.create_task(first_service.run_job(job.id))
    await asyncio.wait_for(started.wait(), timeout=2)
    second_result = await second_service.run_job(job.id)
    release.set()

    assert await first_run is True
    assert second_result is False
    assert calls == [job.id]
    loaded = CronService(store_path).get_job(job.id)
    assert loaded is not None
    assert len(loaded.state.run_history) == 1
    assert loaded.state.running_token is None


@pytest.mark.asyncio
async def test_execution_outcome_appends_to_fresh_run_history(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, on_job=lambda _: asyncio.sleep(0))
    job = service.add_job(
        name="merge-history",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    detached = service._claim_job(job.id, force=False)
    assert detached is not None
    delete_after_run = await service._execute_job(detached)

    external_record = CronRunRecord(run_at_ms=1, status="ok", duration_ms=2)

    def append_external(store) -> None:
        store.jobs[0].state.run_history.append(external_record)

    CronService(store_path)._mutate_store(append_external)
    service._persist_execution_outcomes([(detached, delete_after_run)])

    loaded = CronService(store_path).get_job(job.id)
    assert loaded is not None
    assert loaded.state.run_history[0] == external_record
    assert len(loaded.state.run_history) == 2


@pytest.mark.asyncio
async def test_execution_outcome_stays_bound_to_original_store_after_rebind(tmp_path) -> None:
    old_store = tmp_path / "old" / "cron" / "jobs.json"
    new_store = tmp_path / "new" / "cron" / "jobs.json"
    started = asyncio.Event()
    release = asyncio.Event()

    async def on_job(_job: CronJob) -> None:
        started.set()
        await release.wait()

    service = CronService(old_store, on_job=on_job)
    job = service.add_job(
        name="old-workspace",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    run = asyncio.create_task(service.run_job(job.id))
    await asyncio.wait_for(started.wait(), timeout=2)

    await service.rebind_store_async(new_store)
    release.set()

    assert await run is True
    old_job = CronService(old_store).get_job(job.id)
    assert old_job is not None
    assert len(old_job.state.run_history) == 1
    assert old_job.state.running_token is None
    assert CronService(new_store).list_jobs(include_disabled=True) == []


@pytest.mark.asyncio
async def test_event_loop_store_mutation_does_not_wait_on_thread_lock(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    lock = _thread_lock_for(service._lock_path)
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with lock:
            acquired.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert acquired.wait(timeout=2)
    started_at = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="timed out locking cron store"):
            service.add_job(
                name="contended",
                schedule=CronSchedule(kind="every", every_ms=60_000),
                message="hello",
            )
    finally:
        release.set()
        thread.join(timeout=2)

    assert time.monotonic() - started_at < 0.5


@pytest.mark.asyncio
async def test_async_store_operation_waits_off_event_loop(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    lock = _thread_lock_for(service._lock_path)
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with lock:
            acquired.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert acquired.wait(timeout=2)
    try:
        operation = asyncio.create_task(
            service.run_store_io(
                service.add_job,
                name="off-loop",
                schedule=CronSchedule(kind="every", every_ms=60_000),
                message="hello",
            )
        )
        await asyncio.sleep(0.05)
        assert not operation.done()
        release.set()
        job = await asyncio.wait_for(operation, timeout=2)
    finally:
        release.set()
        thread.join(timeout=2)

    assert job.name == "off-loop"


@pytest.mark.asyncio
async def test_queued_store_operation_remains_bound_to_submission_store(tmp_path) -> None:
    old_store = tmp_path / "old" / "cron" / "jobs.json"
    new_store = tmp_path / "new" / "cron" / "jobs.json"
    service = CronService(old_store)
    lock = _thread_lock_for(service._lock_path)
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with lock:
            acquired.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert acquired.wait(timeout=2)
    try:
        operation = asyncio.create_task(
            service.run_store_io(
                service.add_job,
                name="queued-old",
                schedule=CronSchedule(kind="every", every_ms=60_000),
                message="hello",
            )
        )
        await asyncio.sleep(0.05)
        await service.rebind_store_async(new_store)
        release.set()
        await asyncio.wait_for(operation, timeout=2)
    finally:
        release.set()
        thread.join(timeout=2)

    assert [job.name for job in CronService(old_store).list_jobs()] == ["queued-old"]
    assert CronService(new_store).list_jobs(include_disabled=True) == []


@pytest.mark.asyncio
async def test_cancelled_waiting_store_operation_does_not_commit_later(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    lock = _thread_lock_for(service._lock_path)
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with lock:
            acquired.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert acquired.wait(timeout=2)
    try:
        operation = asyncio.create_task(
            service.run_store_io(
                service.add_job,
                name="cancelled",
                schedule=CronSchedule(kind="every", every_ms=60_000),
                message="hello",
            )
        )
        await asyncio.sleep(0.05)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, timeout=2)
    finally:
        release.set()
        thread.join(timeout=2)

    assert CronService(store_path).list_jobs(include_disabled=True) == []


@pytest.mark.asyncio
async def test_repeated_cancellation_before_atomic_replace_does_not_commit(
    tmp_path, monkeypatch
) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    write_started = threading.Event()
    release_write = threading.Event()
    original_write = cron_service_module._write_text_atomic

    def blocking_write(*args, **kwargs) -> None:
        write_started.set()
        release_write.wait(timeout=5)
        original_write(*args, **kwargs)

    monkeypatch.setattr(cron_service_module, "_write_text_atomic", blocking_write)
    operation = asyncio.create_task(
        service.run_store_io(
            service.add_job,
            name="cancel-wins",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="hello",
        )
    )
    while not write_started.is_set():
        await asyncio.sleep(0)

    operation.cancel()
    await asyncio.sleep(0)
    operation.cancel()
    release_write.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, timeout=2)
    assert CronService(store_path).list_jobs(include_disabled=True) == []


@pytest.mark.asyncio
async def test_cancellation_is_not_lost_during_precommit_check(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    check_started = threading.Event()
    release_check = threading.Event()
    original_check = cron_service_module._StoreOperationState.check_cancelled

    def blocking_check(state) -> None:
        if not check_started.is_set():
            with state._commit_lock:
                check_started.set()
                release_check.wait(timeout=5)
        original_check(state)

    monkeypatch.setattr(
        cron_service_module._StoreOperationState,
        "check_cancelled",
        blocking_check,
    )
    operation = asyncio.create_task(
        service.run_store_io(
            service.add_job,
            name="must-cancel-before-mutator",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="hello",
        )
    )
    while not check_started.is_set():
        await asyncio.sleep(0)

    operation.cancel()
    await asyncio.sleep(0)
    release_check.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, timeout=2)
    assert CronService(store_path).list_jobs(include_disabled=True) == []


@pytest.mark.asyncio
async def test_atomic_replace_in_progress_wins_over_cancellation(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    replace_started = threading.Event()
    release_replace = threading.Event()
    original_replace = Path.replace

    def blocking_replace(path: Path, target: Path) -> Path:
        replace_started.set()
        release_replace.wait(timeout=5)
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", blocking_replace)
    operation = asyncio.create_task(
        service.run_store_io(
            service.add_job,
            name="commit-wins",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="hello",
        )
    )
    while not replace_started.is_set():
        await asyncio.sleep(0)

    operation.cancel()
    await asyncio.sleep(0)
    release_replace.set()

    job = await asyncio.wait_for(operation, timeout=2)
    assert job.name == "commit-wins"
    assert [saved.name for saved in CronService(store_path).list_jobs()] == ["commit-wins"]


@pytest.mark.asyncio
async def test_successful_rebind_wins_over_cancellation_and_continues_caller(
    tmp_path, monkeypatch
) -> None:
    old_store = tmp_path / "old" / "cron" / "jobs.json"
    new_store = tmp_path / "new" / "cron" / "jobs.json"
    service = CronService(old_store)
    published = threading.Event()
    release_worker = threading.Event()
    continued = asyncio.Event()
    original_rebind = service.rebind_store

    def blocking_rebind(path: Path) -> None:
        original_rebind(path)
        published.set()
        release_worker.wait(timeout=5)

    monkeypatch.setattr(service, "rebind_store", blocking_rebind)

    async def reload_runtime() -> None:
        await service.rebind_store_async(new_store)
        continued.set()

    reload_task = asyncio.create_task(reload_runtime())
    while not published.is_set():
        await asyncio.sleep(0)
    reload_task.cancel()
    await asyncio.sleep(0)
    release_worker.set()

    await asyncio.wait_for(reload_task, timeout=2)
    assert continued.is_set()
    assert service.store_path == new_store


@pytest.mark.asyncio
async def test_rebind_drains_old_timer_before_publishing_new_store(tmp_path) -> None:
    old_store = tmp_path / "old" / "cron" / "jobs.json"
    new_store = tmp_path / "new" / "cron" / "jobs.json"
    callback_paths: list[Path] = []

    async def on_job(_job: CronJob) -> None:
        callback_paths.append(service.store_path)

    service = CronService(old_store, on_job=on_job)
    job = service.add_job(
        name="old-due-job",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )

    def make_due(store) -> None:
        store.jobs[0].state.next_run_at_ms = 1

    service._mutate_store(make_due)
    service._running = True
    service._event_loop = asyncio.get_running_loop()
    old_lock = _thread_lock_for(service._lock_path)
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_old_store_lock() -> None:
        with old_lock:
            lock_acquired.set()
            release_lock.wait(timeout=5)

    lock_thread = threading.Thread(target=hold_old_store_lock)
    lock_thread.start()
    assert lock_acquired.wait(timeout=2)
    try:
        service._arm_timer(delay_override_ms=0)
        while not service._timer_execution_tasks:
            await asyncio.sleep(0)

        rebind = asyncio.create_task(service.rebind_store_async(new_store))
        await asyncio.sleep(0.05)
        assert not rebind.done()
        release_lock.set()
        await asyncio.wait_for(rebind, timeout=2)
    finally:
        release_lock.set()
        lock_thread.join(timeout=2)
        service.stop()

    assert callback_paths == []
    assert service.store_path == new_store
    old_job = CronService(old_store).get_job(job.id)
    assert old_job is not None
    assert old_job.state.running_token is None


@pytest.mark.asyncio
async def test_failed_rebind_keeps_old_store_and_timer(tmp_path, monkeypatch) -> None:
    old_store = tmp_path / "old" / "cron" / "jobs.json"
    new_store = tmp_path / "new" / "cron" / "jobs.json"
    service = CronService(old_store)
    service.add_job(
        name="old-job",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    await service.start()
    old_timer = service._timer_task
    original_write = cron_service_module._write_text_atomic

    def fail_new_store(path, *args, **kwargs) -> None:
        if path == new_store:
            raise OSError("target unavailable")
        original_write(path, *args, **kwargs)

    monkeypatch.setattr(cron_service_module, "_write_text_atomic", fail_new_store)
    try:
        with pytest.raises(OSError, match="target unavailable"):
            await service.rebind_store_async(new_store)

        assert service.store_path == old_store
        assert old_timer is not None and old_timer.cancelled()
        assert service._timer_task is not None
        assert service._timer_task is not old_timer
        assert not service._timer_task.done()
        assert [job.name for job in service.list_jobs()] == ["old-job"]
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_timer_failure_rearms_short_retry(tmp_path, monkeypatch) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    service._running = True
    delays: list[int | None] = []

    def fail_claim(_now_ms: int) -> list[CronJob]:
        raise OSError("store unavailable")

    monkeypatch.setattr(service, "_claim_due_jobs", fail_claim)
    monkeypatch.setattr(
        service,
        "_arm_timer",
        lambda *, delay_override_ms=None: delays.append(delay_override_ms),
    )

    await service._on_timer()

    assert delays == [250]


@pytest.mark.asyncio
async def test_timer_store_writes_do_not_block_event_loop(tmp_path, monkeypatch) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json", on_job=lambda _: asyncio.sleep(0))
    service.add_job(
        name="due",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    service._mutate_store(lambda store: setattr(store.jobs[0].state, "next_run_at_ms", 1))
    original_write = cron_service_module._write_text_atomic

    def slow_write(*args, **kwargs) -> None:
        time.sleep(0.15)
        original_write(*args, **kwargs)

    monkeypatch.setattr(cron_service_module, "_write_text_atomic", slow_write)

    tick = asyncio.create_task(service._on_timer())
    started_at = time.monotonic()
    await asyncio.sleep(0.02)

    assert time.monotonic() - started_at < 0.1
    await asyncio.wait_for(tick, timeout=2)


@pytest.mark.asyncio
async def test_completed_side_effect_keeps_claim_when_outcome_commit_fails(
    tmp_path, monkeypatch
) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    calls: list[str] = []

    async def on_job(job: CronJob) -> None:
        calls.append(job.id)

    service = CronService(store_path, on_job=on_job)
    job = service.add_job(
        name="commit-failure",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    service._mutate_store(lambda store: setattr(store.jobs[0].state, "next_run_at_ms", 1))

    def fail_commit(_outcomes) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(service, "_persist_execution_outcomes", fail_commit)

    await service._on_timer()

    loaded = CronService(store_path).get_job(job.id)
    assert calls == [job.id]
    assert loaded is not None
    assert loaded.state.running_token is not None
    assert loaded.state.running_at_ms is not None


@pytest.mark.asyncio
async def test_stop_during_timer_execution_releases_claim(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    started = asyncio.Event()
    never = asyncio.Event()

    async def on_job(_job: CronJob) -> None:
        started.set()
        await never.wait()

    service = CronService(store_path, on_job=on_job)
    job = service.add_job(
        name="cancelled",
        schedule=CronSchedule(kind="every", every_ms=1),
        message="hello",
    )
    await asyncio.sleep(0.01)
    service._running = True
    timer_task = asyncio.create_task(service._on_timer())
    service._timer_task = timer_task
    await asyncio.wait_for(started.wait(), timeout=2)

    service.stop()
    with pytest.raises(asyncio.CancelledError):
        await timer_task

    loaded = CronService(store_path).get_job(job.id)
    assert loaded is not None
    assert loaded.state.running_token is None
    assert loaded.state.running_at_ms is None


@pytest.mark.asyncio
async def test_rearming_timer_does_not_cancel_executing_job(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    cancellations = 0

    async def on_job(_job: CronJob) -> None:
        nonlocal calls, cancellations
        calls += 1
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancellations += 1
            raise

    service = CronService(store_path, on_job=on_job)
    service.add_job(
        name="running",
        schedule=CronSchedule(kind="at", at_ms=int(time.time() * 1000) + 30),
        message="hello",
    )
    await service.start()
    try:
        await asyncio.wait_for(started.wait(), timeout=2)

        service.add_job(
            name="new-job",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            message="later",
        )
        await asyncio.sleep(0.05)

        assert calls == 1
        assert cancellations == 0
        release.set()
        await asyncio.sleep(0.05)
    finally:
        release.set()
        service.stop()


def test_cron_job_from_dict_rehydrates_run_history() -> None:
    job = CronJob.from_dict(
        {
            "id": "job-1",
            "name": "demo",
            "schedule": {"kind": "every", "every_ms": 1000},
            "payload": {"kind": "agent_turn", "message": "hi"},
            "state": {
                "run_history": [
                    {
                        "run_at_ms": 1,
                        "status": "ok",
                        "duration_ms": 2,
                        "error": None,
                    }
                ]
            },
        }
    )

    assert isinstance(job.state.run_history[0], CronRunRecord)


def test_store_state_skips_null_and_malformed_run_history_records() -> None:
    state = CronJobState.from_store_dict(
        {
            "runHistory": [
                None,
                "bad",
                {},
                {"runAtMs": 1, "status": "ok", "durationMs": 2},
            ]
        }
    )

    assert state.run_history == [CronRunRecord(run_at_ms=1, status="ok", duration_ms=2)]


def test_store_loader_accepts_camel_and_snake_case_with_null_numbers(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "compat",
                        "name": "compat",
                        "schedule": {"kind": "every", "every_ms": 1000},
                        "payload": {"message": "hello"},
                        "state": {
                            "next_run_at_ms": None,
                            "run_history": [
                                {
                                    "runAtMs": None,
                                    "status": "ok",
                                    "duration_ms": None,
                                }
                            ],
                        },
                        "createdAtMs": None,
                        "updated_at_ms": "",
                        "delete_after_run": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    job = CronService(store_path).get_job("compat")

    assert job is not None
    assert job.schedule.every_ms == 1000
    assert job.created_at_ms == 0
    assert job.updated_at_ms == 0
    assert job.delete_after_run is True
    assert job.state.run_history[0].run_at_ms == 0
    assert job.state.run_history[0].duration_ms == 0


def test_store_loader_coerces_string_schedule_and_state_timestamps(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "string-ms",
                        "name": "string-ms",
                        "schedule": {"kind": "every", "everyMs": "60000"},
                        "payload": {"message": "hello"},
                        "state": {"nextRunAtMs": "100", "lastRunAtMs": "50"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    job = CronService(store_path).get_job("string-ms")

    assert job is not None
    assert job.schedule.every_ms == 60_000
    assert job.state.next_run_at_ms == 100
    assert job.state.last_run_at_ms == 50


@pytest.mark.asyncio
async def test_running_service_picks_up_external_add(tmp_path):
    """A running service should detect and execute a job added by another instance."""
    store_path = tmp_path / "cron" / "jobs.json"
    called: list[str] = []

    async def on_job(job):
        called.append(job.name)

    service = CronService(store_path, on_job=on_job)
    service.add_job(
        name="heartbeat",
        schedule=CronSchedule(kind="every", every_ms=150),
        message="tick",
    )
    await service.start()
    try:
        await asyncio.sleep(0.05)

        external = CronService(store_path)
        external.add_job(
            name="external",
            schedule=CronSchedule(kind="every", every_ms=150),
            message="ping",
        )

        await asyncio.sleep(0.6)
        assert "external" in called
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_manual_run_preserves_running_scheduler_state(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    called: list[str] = []

    async def on_job(job) -> None:
        called.append(job.id)

    service = CronService(store_path, on_job=on_job)
    job = service.add_job(
        name="manual-run",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    service._running = True

    ok = await service.run_job(job.id)

    assert ok is True
    assert called == [job.id]
    assert service._running is True


@pytest.mark.asyncio
async def test_manual_run_does_not_restart_stopped_scheduler(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    started = asyncio.Event()
    release = asyncio.Event()

    async def on_job(_job: CronJob) -> None:
        started.set()
        await release.wait()

    service = CronService(store_path, on_job=on_job)
    job = service.add_job(
        name="manual-stop",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )
    service._running = True
    run = asyncio.create_task(service.run_job(job.id))
    await asyncio.wait_for(started.wait(), timeout=2)

    service.stop()
    release.set()

    assert await run is True
    assert service._running is False
    assert service._timer_task is None


@pytest.mark.asyncio
async def test_running_service_periodically_wakes_for_external_earlier_job(tmp_path) -> None:
    """A long-sleeping scheduler should still notice externally added earlier jobs."""
    store_path = tmp_path / "cron" / "jobs.json"
    called: list[str] = []

    async def on_job(job) -> None:
        called.append(job.name)

    service = CronService(store_path, on_job=on_job, max_sleep_ms=50)
    service.add_job(
        name="far-future",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="later",
    )
    await service.start()
    try:
        await asyncio.sleep(0.02)

        external = CronService(store_path)
        external.add_job(
            name="external-soon",
            schedule=CronSchedule(kind="every", every_ms=80),
            message="soon",
        )

        await asyncio.sleep(0.35)
        assert "external-soon" in called
        assert "far-future" not in called
    finally:
        service.stop()


def test_apply_runtime_config_updates_max_sleep_and_rearms_when_running(
    monkeypatch, tmp_path
) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    service._running = True
    rearmed: list[bool] = []
    monkeypatch.setattr(service, "_arm_timer", lambda: rearmed.append(True))

    service.apply_runtime_config(12_345)

    assert service.max_sleep_ms == 12_345
    assert rearmed == [True]
