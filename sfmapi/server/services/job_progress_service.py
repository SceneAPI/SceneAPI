"""Durable job progress snapshots shared by REST and MCP surfaces."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.db.models import JobEvent, Task
from sfmapi.server.schemas.api.jobs import JobProgressOut, TaskProgressOut
from sfmapi.server.services import job_service

TERMINAL_TASK_STATUSES = {"succeeded", "failed", "cancelled", "cancelled_dirty", "skipped"}


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _elapsed_seconds(
    started_at: datetime | None,
    finished_at: datetime | None,
    now: datetime,
) -> float | None:
    start = _utc(started_at)
    if start is None:
        return None
    end = _utc(finished_at) or now
    return max(0.0, (end - start).total_seconds())


def _event_fraction(
    payload: dict[str, object] | None,
) -> tuple[float | None, int | None, int | None]:
    if not payload or payload.get("kind") != "phase_progress":
        return None, None, None
    current = payload.get("current")
    total = payload.get("total")
    if not isinstance(current, int) or not isinstance(total, int) or total <= 0:
        return None, current if isinstance(current, int) else None, None
    return max(0.0, min(1.0, current / total)), current, total


def _task_fraction(
    task: Task,
    latest_payload: dict[str, object] | None,
) -> tuple[float, int | None, int | None]:
    if task.status in TERMINAL_TASK_STATUSES:
        return 1.0, None, None
    fraction, current, total = _event_fraction(latest_payload)
    return (fraction if fraction is not None else 0.0), current, total


async def get_job_progress(
    session: AsyncSession,
    *,
    tenant_id: str,
    job_id: str,
) -> JobProgressOut:
    """Return a compact, polling-friendly progress snapshot for one job."""
    j = await job_service.get_job(session, tenant_id=tenant_id, job_id=job_id)
    tasks = (
        (await session.execute(select(Task).where(Task.job_id == job_id).order_by(Task.created_at)))
        .scalars()
        .all()
    )
    events_desc = (
        (
            await session.execute(
                select(JobEvent)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.event_id.desc())
                .limit(1000)
            )
        )
        .scalars()
        .all()
    )
    latest_event = events_desc[0] if events_desc else None
    latest_payload = dict(latest_event.payload_json or {}) if latest_event is not None else None

    latest_by_task: dict[str, JobEvent] = {}
    for event in events_desc:
        payload = event.payload_json or {}
        task_id = payload.get("task_id")
        if isinstance(task_id, str) and task_id not in latest_by_task:
            latest_by_task[task_id] = event

    now = datetime.now(UTC)
    task_counts: dict[str, int] = {}
    task_reports: list[TaskProgressOut] = []
    progress_sum = 0.0
    for task in tasks:
        task_counts[task.status] = task_counts.get(task.status, 0) + 1
        task_event = latest_by_task.get(task.task_id)
        task_payload = dict(task_event.payload_json or {}) if task_event is not None else None
        fraction, current, total = _task_fraction(task, task_payload)
        progress_sum += fraction
        task_reports.append(
            TaskProgressOut(
                task_id=task.task_id,
                kind=task.kind,
                status=task.status,
                progress=fraction,
                phase=task_payload.get("phase") if task_payload else None,
                current=current,
                total=total,
                latest_event_id=task_event.event_id if task_event is not None else None,
                latest_event_kind=task_payload.get("kind") if task_payload else None,
                started_at=task.started_at,
                finished_at=task.finished_at,
                elapsed_seconds=_elapsed_seconds(task.started_at, task.finished_at, now),
            )
        )

    completed_tasks = sum(1 for task in tasks if task.status in TERMINAL_TASK_STATUSES)
    if tasks:
        overall_progress = progress_sum / len(tasks)
    else:
        overall_progress = 1.0 if j.status in {"succeeded", "failed", "cancelled"} else 0.0

    current_task = next((task for task in tasks if task.status == "running"), None)
    if current_task is None:
        current_task = next((task for task in tasks if task.status == "pending"), None)
    current_phase = latest_payload.get("phase") if latest_payload else None

    return JobProgressOut(
        job_id=j.job_id,
        recipe=j.recipe,
        status=j.status,
        progress=max(0.0, min(1.0, overall_progress)),
        total_tasks=len(tasks),
        completed_tasks=completed_tasks,
        task_counts=task_counts,
        current_task_id=current_task.task_id if current_task is not None else None,
        current_task_kind=current_task.kind if current_task is not None else None,
        current_phase=current_phase,
        latest_event_id=latest_event.event_id if latest_event is not None else None,
        latest_event=latest_payload,
        tasks=task_reports,
    )
