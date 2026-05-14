"""Lease-reclaim janitor — recover tasks orphaned by a dead worker.

``app/workers/dispatcher.py`` acquires a lease on every task it runs and
refreshes it on a heartbeat (every ``lease_ttl_seconds // 3`` seconds). If
the worker process dies, the heartbeat stops and the lease ages out — but
the task row stays ``running`` forever, because nothing else watches it.

This module sweeps for those expired leases, resets the task to
``pending``, and re-enqueues it so a live worker picks it up. The sweep is
driven by a background loop in ``app/main.py::lifespan`` (``_janitor_loop``);
:func:`reclaim_expired_leases` is kept as a pure, unit-testable DB function
with no queue side effect, and :func:`run_janitor_once` adds the re-enqueue.
"""

from __future__ import annotations

import contextlib

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Task
from app.orchestrator.lease import now_utc
from app.orchestrator.queue import get_queue

_log = get_logger("orchestrator.janitor")


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
    rows = (
        (
            await session.execute(
                select(Task.task_id).where(
                    Task.status == "running",
                    Task.lease_expires_at.is_not(None),
                    Task.lease_expires_at < now,
                )
            )
        )
        .scalars()
        .all()
    )
    reclaimed = [str(task_id) for task_id in rows]
    if reclaimed:
        await session.execute(
            update(Task.__table__)
            .where(Task.task_id.in_(reclaimed))
            .values(status="pending", worker_id=None, lease_expires_at=None)
        )
        await session.commit()
    return reclaimed


async def run_janitor_once(session: AsyncSession) -> list[str]:
    """Reclaim expired leases, re-enqueue the reclaimed tasks, and GC
    expired uploads.

    ARQ is push-based — a bare status reset would leave the task
    ``pending`` with no queue message, and no worker would ever pick it
    up. Re-enqueue failures are tolerated (Redis may be transiently
    down): the task stays ``pending`` and the next sweep retries.

    The same sweep also drops uploads past ``expires_at`` that were
    never finalized (and their temp bytes) — this is what backs the
    ``UploadState`` doc's "expired ... reaped by the janitor" claim.
    Returns the reclaimed task ids.
    """
    reclaimed = await reclaim_expired_leases(session)
    if reclaimed:
        queue = get_queue()
        try:
            for task_id in reclaimed:
                with contextlib.suppress(Exception):
                    await queue.enqueue(task_id)
        finally:
            await queue.close()
        _log.info("janitor.reclaimed", count=len(reclaimed), task_ids=reclaimed)

    # Sweep expired, never-finalized uploads. Tolerate failures — the
    # next tick retries; a transient DB hiccup must not kill the loop.
    with contextlib.suppress(Exception):
        from app.services.upload_service import gc_expired_uploads

        expired = await gc_expired_uploads(session)
        await session.commit()
        if expired:
            _log.info("janitor.uploads_expired", count=expired)

    return reclaimed
