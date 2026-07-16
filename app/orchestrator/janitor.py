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
"""

from __future__ import annotations

import contextlib

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Reconstruction, Task
from app.orchestrator.lease import now_utc
from app.orchestrator.queue import get_queue
from app.services import job_service

_log = get_logger("orchestrator.janitor")


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


async def find_ready_pending_tasks(session: AsyncSession) -> list[str]:
    """Return pending tasks whose dependencies have reached a reusable terminal state.

    ARQ is push-based, so a pending task with no queue message will never
    run unless some background sweep re-enqueues it. Duplicate delivery is
    tolerated by the dispatcher lease/status gates.
    """
    rows = (await session.execute(select(Task))).scalars().all()
    status_by_id = {str(task.task_id): task.status for task in rows}
    ready: list[str] = []
    for task in rows:
        if task.status != "pending":
            continue
        if task.task_state_json is None:
            continue
        deps = [str(dep) for dep in (task.depends_on_json or [])]
        if all(status_by_id.get(dep) in {"succeeded", "skipped"} for dep in deps):
            ready.append(str(task.task_id))
    return ready


async def propagate_terminal_dependencies(session: AsyncSession) -> list[str]:
    """Mark pending tasks terminal when an upstream dependency is terminal.

    This closes the crash window after one task commits failure/cancellation
    but before the dispatcher advances the rest of the DAG. Without this sweep,
    downstream tasks stay pending forever because they can never become
    dependency-ready.
    """
    rows = (await session.execute(select(Task))).scalars().all()
    status_by_id = {str(task.task_id): task.status for task in rows}
    changed: list[str] = []
    affected_jobs: set[str] = set()
    affected_reconstructions: dict[tuple[str, str], str] = {}
    progressed = True
    while progressed:
        progressed = False
        for task in rows:
            task_id = str(task.task_id)
            if status_by_id.get(task_id) != "pending":
                continue
            deps = [str(dep) for dep in (task.depends_on_json or [])]
            finished_at = now_utc()
            if any(dep not in status_by_id for dep in deps):
                task.status = "failed"
                task.error_class = "DependencyFailed"
                task.error_message = "upstream dependency missing"
                task.finished_at = finished_at
            elif any(status_by_id.get(dep) == "failed" for dep in deps):
                task.status = "failed"
                task.error_class = "DependencyFailed"
                task.error_message = "upstream dependency failed"
                task.finished_at = finished_at
            elif any(status_by_id.get(dep) == "cancelled_dirty" for dep in deps):
                task.status = "cancelled_dirty"
                task.error_class = "DependencyCancelled"
                task.error_message = "upstream dependency cancelled"
                task.finished_at = finished_at
            elif any(status_by_id.get(dep) == "cancelled" for dep in deps):
                task.status = "cancelled"
                task.error_class = "DependencyCancelled"
                task.error_message = "upstream dependency cancelled"
                task.finished_at = finished_at
            else:
                continue
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


async def run_janitor_once(session: AsyncSession) -> list[str]:
    """Reclaim expired leases, re-enqueue ready tasks, and GC expired uploads.

    ARQ is push-based: a bare status reset, or a submit-time enqueue
    failure, can leave a task ``pending`` with no queue message. Re-enqueue
    failures are tolerated (Redis may be transiently down): the task stays
    ``pending`` and the next sweep retries.

    The same sweep also drops uploads past ``expires_at`` that were
    never finalized (and their temp bytes) — this is what backs the
    ``UploadState`` doc's "expired ... reaped by the janitor" claim.
    Returns the reclaimed task ids.
    """
    reclaimed = await reclaim_expired_leases(session)
    terminal_deps = await propagate_terminal_dependencies(session)
    ready_pending = await find_ready_pending_tasks(session)
    enqueue_ids = list(dict.fromkeys([*reclaimed, *ready_pending]))
    if enqueue_ids:
        queue = get_queue()
        try:
            for task_id in enqueue_ids:
                with contextlib.suppress(Exception):
                    await queue.enqueue(task_id)
        finally:
            await queue.close()
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

    return reclaimed
