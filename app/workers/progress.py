"""Worker-side ``ProgressEvent`` persistence."""

from __future__ import annotations

import asyncio
from asyncio import AbstractEventLoop
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.adapters.progress import LogLevel, ProgressReporter
from app.core.logging import get_logger
from app.db.models import JobEvent
from app.db.session import get_session_factory
from app.schemas.progress_event import Phase, ProgressEvent
from app.workers.events import JsonlEventSink, now_iso

_CURRENT_REPORTER: ContextVar[ProgressReporter | None] = ContextVar(
    "sfmapi_current_progress_reporter",
    default=None,
)
_EVENT_ADAPTER: TypeAdapter[Any] = TypeAdapter(ProgressEvent)


def get_progress_reporter() -> ProgressReporter | None:
    """Return the reporter bound to the currently running worker task."""

    return _CURRENT_REPORTER.get()


def set_progress_reporter(reporter: ProgressReporter | None) -> Token[ProgressReporter | None]:
    """Bind ``reporter`` for the next ``asyncio.to_thread`` handler call."""

    return _CURRENT_REPORTER.set(reporter)


def reset_progress_reporter(token: Token[ProgressReporter | None]) -> None:
    """Restore the previous bound reporter."""

    _CURRENT_REPORTER.reset(token)


async def append_job_event(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one validated ``ProgressEvent`` and return its JSON payload.

    ``JobEvent.event_id`` is supplied explicitly so the same code works
    on SQLite, where ``BigInteger`` primary keys do not autoincrement as
    rowids. A short retry loop handles rare concurrent inserts.
    """

    factory = get_session_factory()
    now = datetime.now(UTC)
    last_error: Exception | None = None
    for _attempt in range(5):
        async with factory() as session:
            max_id = (await session.execute(select(func.max(JobEvent.event_id)))).scalar_one()
            event_id = int(max_id or 0) + 1
            stored = _validated_payload(payload, seq=event_id)
            session.add(
                JobEvent(
                    event_id=event_id,
                    job_id=job_id,
                    ts=now,
                    payload_json=stored,
                )
            )
            try:
                await session.commit()
                return stored
            except IntegrityError as exc:
                last_error = exc
                await session.rollback()
    raise RuntimeError("could not allocate a unique job event id") from last_error


def _validated_payload(payload: dict[str, Any], *, seq: int) -> dict[str, Any]:
    candidate = dict(payload)
    candidate["seq"] = seq
    validated = _EVENT_ADAPTER.validate_python(candidate)
    return cast(dict[str, Any], validated.model_dump(mode="json"))


class WorkerProgressReporter:
    """Synchronous reporter used by worker task handlers.

    Handlers run in a worker thread. Event persistence is scheduled back
    onto the dispatcher's asyncio loop, then mirrored to ``events.jsonl``
    after the DB row is committed.
    """

    def __init__(
        self,
        *,
        job_id: str,
        task_id: str,
        loop: AbstractEventLoop,
        event_path: Path | None = None,
    ) -> None:
        self.job_id = job_id
        self.task_id = task_id
        self._loop = loop
        self._sink = JsonlEventSink(event_path) if event_path is not None else None
        self._log = get_logger("worker.progress").bind(job_id=job_id, task_id=task_id)

    def phase_started(self, phase: Phase) -> None:
        self._emit({"kind": "phase_started", "phase": phase})

    def phase_progress(
        self,
        phase: Phase,
        *,
        current: int,
        total: int | None = None,
        rate: float | None = None,
    ) -> None:
        self._emit(
            {
                "kind": "phase_progress",
                "phase": phase,
                "current": max(0, int(current)),
                "total": total if total is None else max(0, int(total)),
                "rate": rate,
            }
        )

    def phase_completed(self, phase: Phase) -> None:
        self._emit({"kind": "phase_completed", "phase": phase})

    def metric(self, key: str, value: float) -> None:
        self._emit({"kind": "metric", "key": key, "value": float(value)})

    def snapshot_available(self, *, snapshot_seq: int, summary: dict[str, Any]) -> None:
        self._emit(
            {
                "kind": "snapshot_available",
                "snapshot_seq": int(snapshot_seq),
                "summary": summary,
            }
        )

    def log_line(self, level: LogLevel, message: str) -> None:
        self._emit({"kind": "log_line", "level": level, "message": message})

    def warning(self, message: str) -> None:
        self._emit({"kind": "warning", "message": message})

    def error(
        self,
        *,
        error_class: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self._emit(
            {
                "kind": "error",
                "error_class": error_class,
                "message": message,
                "detail": detail,
            }
        )

    def _emit(self, fields: dict[str, Any]) -> None:
        payload = {
            "schema_version": 1,
            "ts": now_iso(),
            "job_id": self.job_id,
            "task_id": self.task_id,
            **fields,
        }
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            self._log.warning("progress.emit_skipped_on_dispatch_loop")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                append_job_event(self.job_id, payload),
                self._loop,
            )
            stored = future.result(timeout=10.0)
            if self._sink is not None:
                self._sink.append(stored)
        except Exception as exc:
            self._log.warning("progress.emit_failed", err=str(exc))


__all__ = [
    "WorkerProgressReporter",
    "append_job_event",
    "get_progress_reporter",
    "reset_progress_reporter",
    "set_progress_reporter",
]
