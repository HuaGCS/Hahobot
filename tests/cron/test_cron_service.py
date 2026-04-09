import asyncio
import json

import pytest

from hahobot.cron.service import CronService
from hahobot.cron.types import CronJob, CronPayload, CronRunRecord, CronSchedule


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
    service.register_system_job(CronJob(
        id="dream",
        name="dream",
        schedule=CronSchedule(kind="cron", expr="0 */2 * * *", tz="UTC"),
        payload=CronPayload(kind="system_event"),
    ))

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
