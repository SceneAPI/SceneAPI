"""Lease-reclaim janitor — recover tasks orphaned by a dead worker.

``app/workers/dispatcher.py`` acquires a lease on every task it runs and
refreshes it on a heartbeat (every ``lease_ttl_seconds // 3`` seconds). If
the worker process dies, the heartbeat stops and the lease ages out — but
the task row stays ``running`` forever, because nothing else watches it.

This module sweeps for those expired leases, resets the task to
``pending``, and re-enqueues it so a live worker picks it up. It also
re-enqueues dependency-ready pending tasks; that closes the gap where an
accepted job was committed but Redis/ARQ enqueue failed before a queue
message was created. The sweep is driven by a background loop in
``app/main.py::lifespan`` (``_janitor_loop``); the DB predicates are kept as
pure, unit-testable functions, and :func:`run_janitor_once` adds the
re-enqueue side effect.

The dependency sweeps filter in SQL: only ``pending`` task rows plus the
specific dependency rows they reference are fetched (deps may cross job
boundaries, so the dep lookup is by task id, not job id). Both queries
are served by the composite ``ix_task_status_lease`` index.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.paths import Paths
from app.db.models import Job, JobEvent, Reconstruction, StageArtifact, Task
from app.orchestrator.lease import now_utc
from app.orchestrator.queue import get_shared_queue
from app.orchestrator.readiness import dependencies_ready, dependency_state
from app.services import job_service

_log = get_logger("orchestrator.janitor")

#: Job statuses eligible for retention GC. ``cancelled_dirty`` never
#: appears on Job rows today (task-level only), but is included so a
#: future rollup change can't strand records.
TERMINAL_JOB_STATUSES: tuple[str, ...] = ("succeeded", "failed", "cancelled", "cancelled_dirty")


def _task_recon_id(task: Task) -> str | None:
    state = task.task_state_json if isinstance(task.task_state_json, dict) else {}
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    recon_id = inputs.get("recon_id")
    return recon_id if isinstance(recon_id, str) and recon_id else None


def _resource_status_for_task(task: Task) -> str | None:
    if task.status == "failed":
        return "failed"
    if task.status in {"cancelled", "cancelled_dirty"}:
        return task.status
    return None


async def reclaim_expired_leases(session: AsyncSession) -> list[str]:
    """Reset ``running`` tasks whose lease has expired back to ``pending``.

    An expired lease means the owning worker missed at least two
    consecutive heartbeats (the TTL is 3x the heartbeat interval) — i.e.
    the worker is genuinely gone, not merely slow. Clears ``worker_id``
    and ``lease_expires_at`` so the next ``try_acquire_lease`` succeeds.

    Returns the reclaimed task ids. Pure DB — no re-enqueue side effect,
    so it can be unit-tested without a queue.
    """
    now = now_utc()
    result = await session.execute(
        update(Task)
        .where(
            Task.status == "running",
            Task.lease_expires_at.is_not(None),
            Task.lease_expires_at < now,
        )
        .values(status="pending", worker_id=None, lease_expires_at=None)
        .returning(Task.task_id)
    )
    reclaimed = [str(task_id) for task_id in result.scalars().all()]
    await session.commit()
    return reclaimed


async def _load_pending_tasks_with_dep_statuses(
    session: AsyncSession,
) -> tuple[list[Task], dict[str, str]]:
    """Fetch ``pending`` task rows plus a status map covering them and
    every task they depend on.

    Filters in SQL instead of scanning the whole table: the pending rows
    come from an indexed status predicate, and only the referenced
    dependency rows are loaded (by id — deps may live in other jobs).
    A dep id absent from the returned map is missing from the DB.
    """
    pending = (await session.execute(select(Task).where(Task.status == "pending"))).scalars().all()
    status_by_id = {str(task.task_id): task.status for task in pending}
    dep_ids = {str(dep) for task in pending for dep in (task.depends_on_json or [])}
    unknown = dep_ids - status_by_id.keys()
    if unknown:
        rows = await session.execute(
            select(Task.task_id, Task.status).where(Task.task_id.in_(unknown))
        )
        status_by_id.update({str(task_id): str(status) for task_id, status in rows.all()})
    return list(pending), status_by_id


async def find_ready_pending_tasks(session: AsyncSession) -> list[str]:
    """Return pending tasks whose dependencies have reached a reusable terminal state.

    ARQ is push-based, so a pending task with no queue message will never
    run unless some background sweep re-enqueues it. Duplicate delivery is
    tolerated by the dispatcher lease/status gates.
    """
    pending, status_by_id = await _load_pending_tasks_with_dep_statuses(session)
    ready: list[str] = []
    for task in pending:
        if task.task_state_json is None:
            continue
        if dependencies_ready(task.depends_on_json or [], status_by_id):
            ready.append(str(task.task_id))
    return ready


async def propagate_terminal_dependencies(session: AsyncSession) -> list[str]:
    """Mark pending tasks terminal when an upstream dependency is terminal.

    This closes the crash window after one task commits failure/cancellation
    but before the dispatcher advances the rest of the DAG. Without this sweep,
    downstream tasks stay pending forever because they can never become
    dependency-ready. The fixed-point loop runs in Python over the pending
    subset so terminality cascades within one sweep.
    """
    pending, status_by_id = await _load_pending_tasks_with_dep_statuses(session)
    changed: list[str] = []
    affected_jobs: set[str] = set()
    affected_reconstructions: dict[tuple[str, str], str] = {}
    progressed = True
    while progressed:
        progressed = False
        for task in pending:
            task_id = str(task.task_id)
            if status_by_id.get(task_id) != "pending":
                continue
            deps = [str(dep) for dep in (task.depends_on_json or [])]
            state = dependency_state(deps, status_by_id)
            if state in {"ready", "blocked"}:
                continue
            if state == "failed":
                missing = any(dep not in status_by_id for dep in deps)
                task.status = "failed"
                task.error_class = "DependencyFailed"
                task.error_message = (
                    "upstream dependency missing" if missing else "upstream dependency failed"
                )
            else:  # cancelled / cancelled_dirty
                task.status = state
                task.error_class = "DependencyCancelled"
                task.error_message = "upstream dependency cancelled"
            task.finished_at = now_utc()
            status_by_id[task_id] = task.status
            changed.append(task_id)
            affected_jobs.add(str(task.job_id))
            recon_id = _task_recon_id(task)
            resource_status = _resource_status_for_task(task)
            if recon_id is not None and resource_status is not None:
                affected_reconstructions[(str(task.tenant_id), recon_id)] = resource_status
            progressed = True
    if changed:
        for (tenant_id, recon_id), status in affected_reconstructions.items():
            await session.execute(
                update(Reconstruction)
                .where(
                    Reconstruction.tenant_id == tenant_id,
                    Reconstruction.recon_id == recon_id,
                )
                .values(status=status)
            )
        await session.flush()
        for job_id in affected_jobs:
            await job_service.finalize_job_if_ready(session, job_id=job_id)
        await session.commit()
    return changed


async def gc_expired_job_records(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete terminal Jobs (and their Tasks/artifacts/events) past retention.

    Opt-in via ``settings.retention_days``; ``None`` (the default)
    disables the sweep. A job is eligible when its status is terminal,
    it is not ``pinned``, and ``finished_at`` is older than the cutoff.
    Dependent rows (tasks, stage artifacts, job events) are deleted
    explicitly rather than via ``ON DELETE CASCADE`` — SQLite does not
    enforce FK cascades by default, and the explicit deletes keep both
    engines identical. The job's ``events.jsonl`` file is unlinked when
    present. Does not commit; the caller owns the transaction.

    Returns the number of jobs deleted.
    """
    settings = get_settings()
    if settings.retention_days is None:
        return 0
    cutoff = (now or now_utc()) - timedelta(days=settings.retention_days)
    jobs = (
        (
            await session.execute(
                select(Job).where(
                    Job.status.in_(TERMINAL_JOB_STATUSES),
                    Job.pinned.is_(False),
                    Job.finished_at.is_not(None),
                    Job.finished_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    if not jobs:
        return 0
    paths = Paths(settings)
    for job in jobs:
        events_file = paths.job_root(job.tenant_id, job.project_id, job.job_id) / "events.jsonl"
        with contextlib.suppress(OSError):
            events_file.unlink(missing_ok=True)
    job_ids = [job.job_id for job in jobs]
    await session.execute(delete(StageArtifact).where(StageArtifact.job_id.in_(job_ids)))
    await session.execute(delete(JobEvent).where(JobEvent.job_id.in_(job_ids)))
    await session.execute(delete(Task).where(Task.job_id.in_(job_ids)))
    await session.execute(delete(Job).where(Job.job_id.in_(job_ids)))
    return len(jobs)


async def run_janitor_once(session: AsyncSession) -> list[str]:
    """Reclaim expired leases, re-enqueue ready tasks, and GC expired records.

    ARQ is push-based: a bare status reset, or a submit-time enqueue
    failure, can leave a task ``pending`` with no queue message. Re-enqueue
    failures are tolerated (Redis may be transiently down): the task stays
    ``pending`` and the next sweep retries.

    The same sweep also drops uploads past ``expires_at`` that were
    never finalized (and their temp bytes) — this is what backs the
    ``UploadState`` doc's "expired ... reaped by the janitor" claim —
    and, when ``retention_days`` is set, terminal job records older
    than the retention window. Returns the reclaimed task ids.
    """
    reclaimed = await reclaim_expired_leases(session)
    terminal_deps = await propagate_terminal_dependencies(session)
    ready_pending = await find_ready_pending_tasks(session)
    enqueue_ids = list(dict.fromkeys([*reclaimed, *ready_pending]))
    if enqueue_ids:
        # Shared, process-cached queue — never closed per sweep.
        queue = get_shared_queue()
        for task_id in enqueue_ids:
            with contextlib.suppress(Exception):
                await queue.enqueue(task_id)
        _log.info(
            "janitor.enqueued_ready",
            count=len(enqueue_ids),
            reclaimed=len(reclaimed),
            terminal_deps=len(terminal_deps),
            ready_pending=len(ready_pending),
            task_ids=enqueue_ids,
        )

    # Sweep expired, never-finalized uploads. Tolerate failures — the
    # next tick retries; a transient DB hiccup must not kill the loop.
    with contextlib.suppress(Exception):
        from app.services.upload_service import gc_expired_uploads

        expired = await gc_expired_uploads(session)
        await session.commit()
        if expired:
            _log.info("janitor.uploads_expired", count=expired)

    # Retention GC — delete terminal job records (and their events file)
    # older than ``retention_days``. Opt-in (default off); failures are
    # tolerated per tick exactly like the upload sweep above.
    with contextlib.suppress(Exception):
        removed = await gc_expired_job_records(session)
        await session.commit()
        if removed:
            _log.info("janitor.job_records_expired", count=removed)

    return reclaimed
