"""Cron types."""

from dataclasses import dataclass, field
from typing import Any, Literal


def _get_camel_snake(
    data: dict[str, Any],
    camel: str,
    snake: str,
    default: Any = None,
) -> Any:
    """Read the persisted camelCase form with a snake_case compatibility fallback."""
    if camel in data:
        return data[camel]
    return data.get(snake, default)


def _store_int(value: Any, default: int = 0) -> int:
    """Coerce JSON numerics while treating null/blank like a missing value."""
    if value is None or value == "":
        return default
    return int(value)


@dataclass
class CronSchedule:
    """Schedule definition for a cron job."""

    kind: Literal["at", "every", "cron"]
    # For "at": timestamp in ms
    at_ms: int | None = None
    # For "every": interval in ms
    every_ms: int | None = None
    # For "cron": cron expression (e.g. "0 9 * * *")
    expr: str | None = None
    # Timezone for cron expressions
    tz: str | None = None

    @classmethod
    def from_store_dict(cls, data: dict[str, Any]) -> "CronSchedule":
        return cls(
            kind=data["kind"],
            at_ms=_get_camel_snake(data, "atMs", "at_ms"),
            every_ms=_get_camel_snake(data, "everyMs", "every_ms"),
            expr=data.get("expr"),
            tz=data.get("tz"),
        )


@dataclass
class CronPayload:
    """What to do when the job runs."""

    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    message: str = ""
    # Deliver response to channel
    deliver: bool = False
    channel: str | None = None  # e.g. "whatsapp"
    to: str | None = None  # e.g. phone number

    @classmethod
    def from_store_dict(cls, data: dict[str, Any]) -> "CronPayload":
        return cls(
            kind=data.get("kind", "agent_turn"),
            message=data.get("message", ""),
            deliver=data.get("deliver", False),
            channel=data.get("channel"),
            to=data.get("to"),
        )


@dataclass
class CronRunRecord:
    """A single execution record for a cron job."""

    run_at_ms: int
    status: Literal["ok", "error", "skipped"]
    duration_ms: int = 0
    error: str | None = None

    @classmethod
    def from_store_dict(cls, data: dict[str, Any]) -> "CronRunRecord":
        return cls(
            run_at_ms=_store_int(_get_camel_snake(data, "runAtMs", "run_at_ms", 0)),
            status=data["status"],
            duration_ms=_store_int(_get_camel_snake(data, "durationMs", "duration_ms", 0)),
            error=data.get("error"),
        )


@dataclass
class CronJobState:
    """Runtime state of a job."""

    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: Literal["ok", "error", "skipped"] | None = None
    last_error: str | None = None
    run_history: list[CronRunRecord] = field(default_factory=list)

    @classmethod
    def from_store_dict(cls, data: dict[str, Any]) -> "CronJobState":
        history = _get_camel_snake(data, "runHistory", "run_history", []) or []
        return cls(
            next_run_at_ms=_get_camel_snake(data, "nextRunAtMs", "next_run_at_ms"),
            last_run_at_ms=_get_camel_snake(data, "lastRunAtMs", "last_run_at_ms"),
            last_status=_get_camel_snake(data, "lastStatus", "last_status"),
            last_error=_get_camel_snake(data, "lastError", "last_error"),
            run_history=[
                record
                if isinstance(record, CronRunRecord)
                else CronRunRecord.from_store_dict(record)
                for record in history
            ],
        )


@dataclass
class CronJob:
    """A scheduled job."""

    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = field(default_factory=CronPayload)
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False

    @classmethod
    def from_dict(cls, kwargs: dict):
        state_kwargs = dict(kwargs.get("state", {}))
        state_kwargs["run_history"] = [
            record if isinstance(record, CronRunRecord) else CronRunRecord(**record)
            for record in state_kwargs.get("run_history", [])
        ]
        kwargs["schedule"] = CronSchedule(**kwargs.get("schedule", {"kind": "every"}))
        kwargs["payload"] = CronPayload(**kwargs.get("payload", {}))
        kwargs["state"] = CronJobState(**state_kwargs)
        return cls(**kwargs)

    @classmethod
    def from_store_dict(cls, data: dict[str, Any]) -> "CronJob":
        """Load a job from jobs.json with camelCase and snake_case compatibility."""
        return cls(
            id=data["id"],
            name=data["name"],
            enabled=data.get("enabled", True),
            schedule=CronSchedule.from_store_dict(data["schedule"]),
            payload=CronPayload.from_store_dict(data.get("payload") or {}),
            state=CronJobState.from_store_dict(data.get("state") or {}),
            created_at_ms=_store_int(_get_camel_snake(data, "createdAtMs", "created_at_ms", 0)),
            updated_at_ms=_store_int(_get_camel_snake(data, "updatedAtMs", "updated_at_ms", 0)),
            delete_after_run=bool(
                _get_camel_snake(data, "deleteAfterRun", "delete_after_run", False)
            ),
        )


@dataclass
class CronStore:
    """Persistent store for cron jobs."""

    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)
