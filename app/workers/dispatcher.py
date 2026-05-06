"""Queue-agnostic task dispatcher.

The previous design coupled task execution to ARQ via
``app.workers.runner.run_task`` (which is ARQ's worker entrypoint
shape). This module pulls the actual work — lease acquisition,
handler dispatch, lease heartbeat, status transitions — out into
``execute_task(task_id)`` so any queue backend can drive it.

Backend wrappers stay thin:

  - ``app/workers/runner.py`` (ARQ):
        async def run_task(ctx, task_id): return await execute_task(task_id)

  - A future Celery worker would do:
        @celery_app.task
        def run_task(task_id): asyncio.run(execute_task(task_id))

  - The ``InlineQueue`` plugin invokes ``run_task`` directly inside
    the orchestrator's event loop.

The handler registry lives here too so the dispatcher is the single
place that knows how to map a Task.kind to a handler.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import PycolmapUnavailableError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.db.models import Job, Task
from app.db.session import get_session_factory
from app.orchestrator.lease import now_utc, refresh_lease, try_acquire_lease

WORKER_ID: str = os.environ.get("SFMAPI_WORKER_ID") or new_id()


def _build_handlers() -> dict[str, Callable[..., Any]]:
    """Imported lazily so callers that never invoke a handler (lifespan,
    health checks) don't pay the per-task module load."""
    from app.workers.tasks import (
        ba,
        cubemap,
        dense,
        export,
        extract,
        georegister,
        kapture_import,
        localize,
        match,
        merge_recons,
        mesh,
        noop,
        pgo,
        relocalize,
        render_cubemap,
        triangulate,
        verify,
        video_frames,
        vlad_index,
    )
    from app.workers.tasks import map as map_task

    return {
        "noop": noop.run,
        "extract": extract.run,
        "match": match.run,
        "verify": verify.run,
        "map": map_task.run,
        "ba": ba.run,
        "triangulate": triangulate.run,
        "relocalize": relocalize.run,
        "pgo": pgo.run,
        "export": export.run,
        "vlad_index": vlad_index.run,
        "localize": localize.run,
        "georegister": georegister.run,
        "to_cubemap": cubemap.run,
        "dense": dense.run,
        "render_cubemap": render_cubemap.run,
        "mesh": mesh.run,
        "merge_recons": merge_recons.run,
        "video_frames": video_frames.run,
        "kapture_import": kapture_import.run,
    }


_HANDLERS_CACHE: dict[str, Callable[..., Any]] | None = None


def get_handlers() -> dict[str, Callable[..., Any]]:
    global _HANDLERS_CACHE
    if _HANDLERS_CACHE is None:
        _HANDLERS_CACHE = _build_handlers()
    return _HANDLERS_CACHE


async def execute_task(task_id: str) -> dict:
    """Run one Task end-to-end. Queue-agnostic: any backend that can
    deliver a ``task_id`` can call this.

    Returns one of:
      ``{"status": "missing"}``  — task row gone.
      ``{"status": "busy"}``     — another worker holds the lease.
      ``{"status": "succeeded", "outputs": ...}``
      ``{"status": "failed", "error": ...}``
    """
    log = get_logger("worker.execute_task").bind(task_id=task_id, worker_id=WORKER_ID)
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Task).where(Task.task_id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            return {"status": "missing"}
        acquired = await try_acquire_lease(
            session,
            table=Task.__table__,
            pk_col=Task.task_id,
            lease_col=Task.lease_expires_at,
            worker_col=Task.worker_id,
            pk_value=task_id,
            worker_id=WORKER_ID,
            ttl_seconds=settings.lease_ttl_seconds,
        )
        if not acquired:
            await session.commit()
            log.info("task.lease_busy")
            return {"status": "busy"}
        task.status = "running"
        task.started_at = now_utc()
        await session.commit()

    handler = get_handlers().get(task.kind)
    if handler is None:
        async with factory() as session:
            t = await session.get(Task, task_id)
            t.status = "failed"
            t.error_class = "UnknownTask"
            t.error_message = f"No handler for kind={task.kind}"
            t.finished_at = now_utc()
            await session.commit()
            await _maybe_finalize_job(session, t.job_id)
        return {"status": "failed"}

    heartbeat_task = asyncio.create_task(_heartbeat(task_id))
    try:
        outputs = await asyncio.to_thread(handler, task)
        async with factory() as session:
            t = await session.get(Task, task_id)
            t.status = "succeeded"
            t.outputs_ref_json = outputs or {}
            t.finished_at = now_utc()
            await session.commit()
            await _maybe_finalize_job(session, t.job_id)
        return {"status": "succeeded", "outputs": outputs}
    except PycolmapUnavailableError as e:
        async with factory() as session:
            t = await session.get(Task, task_id)
            t.status = "failed"
            t.error_class = "PycolmapUnavailable"
            t.error_message = str(e)
            t.finished_at = now_utc()
            await session.commit()
            await _maybe_finalize_job(session, t.job_id)
        return {"status": "failed", "error": "pycolmap_unavailable"}
    except Exception as e:
        log.exception("task.failed", err=str(e))
        async with factory() as session:
            t = await session.get(Task, task_id)
            t.status = "failed"
            t.error_class = type(e).__name__
            t.error_message = str(e)[:2000]
            t.finished_at = now_utc()
            await session.commit()
            await _maybe_finalize_job(session, t.job_id)
        return {"status": "failed", "error": str(e)}
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await heartbeat_task


async def _maybe_finalize_job(session: Any, job_id: str) -> None:
    """Roll up Job.status from its constituent Tasks once they're
    all in a terminal state.

    Rules:
      - any Task still pending/running -> Job stays "pending".
      - any Task failed -> Job "failed".
      - any Task cancelled / cancelled_dirty (and none failed) -> Job "cancelled".
      - all succeeded -> Job "succeeded".

    Idempotent: runs after every Task transition; the first call
    with a complete task set wins.
    """
    rows = (await session.execute(select(Task).where(Task.job_id == job_id))).scalars().all()
    if not rows:
        return
    statuses = {t.status for t in rows}
    non_terminal = statuses & {"pending", "running"}
    if non_terminal:
        return  # at least one task still in flight
    if "failed" in statuses:
        new_status = "failed"
    elif statuses & {"cancelled", "cancelled_dirty"}:
        new_status = "cancelled"
    else:
        new_status = "succeeded"
    j = await session.get(Job, job_id)
    if j is None or j.status == new_status:
        return
    j.status = new_status
    j.finished_at = now_utc()
    # Surface the first task error onto the job for convenience.
    if new_status == "failed":
        for t in rows:
            if t.status == "failed":
                j.error_class = t.error_class
                j.error_message = t.error_message
                break
    await session.commit()


async def _heartbeat(task_id: str) -> None:
    settings = get_settings()
    factory = get_session_factory()
    while True:
        await asyncio.sleep(max(1, settings.lease_ttl_seconds // 3))
        async with factory() as session:
            await refresh_lease(
                session,
                table=Task.__table__,
                pk_col=Task.task_id,
                lease_col=Task.lease_expires_at,
                worker_col=Task.worker_id,
                pk_value=task_id,
                worker_id=WORKER_ID,
                ttl_seconds=settings.lease_ttl_seconds,
            )
            await session.commit()
