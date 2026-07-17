"""Queue-agnostic task dispatcher.

The previous design coupled task execution to ARQ via
``sfmapi.server.workers.runner.run_task`` (which is ARQ's worker entrypoint
shape). This module pulls the actual work — lease acquisition,
handler dispatch, lease heartbeat, status transitions — out into
``execute_task(task_id)`` so any queue backend can drive it.

Backend wrappers stay thin:

  - ``sfmapi/server/workers/runner.py`` (ARQ):
        async def run_task(ctx, task_id): return await execute_task(task_id)

  - A future Celery worker would do:
        @celery_app.task
        def run_task(task_id): asyncio.run(execute_task(task_id))

  - The ``InlineQueue`` plugin invokes ``run_task`` directly inside
    the orchestrator's event loop.

The dispatcher knows no task kinds. Handlers auto-register via the
``@task_handler`` decorator, and kind-specific resource roll-ups
(reconstruction / radiance status, success side effects, ...) register
alongside them as ``on_status`` / ``on_success`` lifecycle hooks — the
dispatcher only fires whatever the registry holds for ``task.kind``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from datetime import UTC
from typing import Any

from sqlalchemy import select

from sfmapi.server.core.config import get_settings
from sfmapi.server.core.errors import BackendUnavailableError
from sfmapi.server.core.ids import new_id
from sfmapi.server.core.logging import get_logger
from sfmapi.server.core.paths import Paths
from sfmapi.server.core.public_outputs import sanitize_public_error_message
from sfmapi.server.db.models import Job, Task
from sfmapi.server.db.session import get_session_factory
from sfmapi.server.orchestrator.lease import now_utc, refresh_lease, try_acquire_lease
from sfmapi.server.orchestrator.queue import get_shared_queue
from sfmapi.server.orchestrator.readiness import (
    CANCELLED_DEPENDENCY_STATUSES,
    READY_DEPENDENCY_STATUSES,
)
from sfmapi.server.orchestrator.readiness import (
    dependency_state as _dependency_state_from_statuses,
)
from sfmapi.server.services import artifact_service, dataset_service, job_service
from sfmapi.server.workers.progress import (
    WorkerProgressReporter,
    reset_progress_reporter,
    set_progress_reporter,
)
from sfmapi.server.workers.tasks._registry import get_registered, get_status_hook, get_success_hook

WORKER_ID: str = os.environ.get("SFMAPI_WORKER_ID") or new_id()


def _owns_live_running_lease(task: Task) -> bool:
    lease_expires_at = task.lease_expires_at
    if lease_expires_at is not None and lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=UTC)
    return (
        task.status == "running"
        and task.worker_id == WORKER_ID
        and lease_expires_at is not None
        and lease_expires_at > now_utc()
    )


_TASK_MODULES_IMPORTED = False


def _import_task_modules() -> None:
    """Import every public submodule under ``sfmapi.server.workers.tasks`` once.

    Importing a task module fires its ``@task_handler`` decorator,
    which registers the handler *and* its lifecycle hooks (see
    ``_registry``). Kept lazy so callers that never invoke a handler
    (lifespan, health checks) don't pay the per-task module load, and
    flag-guarded so handler dispatch and hook lookup share one pass.
    """
    global _TASK_MODULES_IMPORTED
    if _TASK_MODULES_IMPORTED:
        return
    import importlib
    import pkgutil

    from sfmapi.server.workers import tasks

    for mod_info in pkgutil.iter_modules(tasks.__path__):
        if mod_info.name.startswith("_"):
            continue
        importlib.import_module(f"sfmapi.server.workers.tasks.{mod_info.name}")
    _TASK_MODULES_IMPORTED = True


def _build_handlers() -> dict[str, Callable[..., Any]]:
    _import_task_modules()
    return get_registered()


_HANDLERS_CACHE: dict[str, Callable[..., Any]] | None = None


def get_handlers() -> dict[str, Callable[..., Any]]:
    global _HANDLERS_CACHE
    if _HANDLERS_CACHE is None:
        _HANDLERS_CACHE = _build_handlers()
    return _HANDLERS_CACHE


async def _run_status_hook(session: Any, task: Task, status: str) -> None:
    """Fire the ``on_status`` hook registered for ``task.kind``, if any.

    Hook lookup triggers the same lazy task-module import as handler
    dispatch, so hooks fire even on paths that never resolve a handler
    (cancel-before-pickup, unknown kind).
    """
    _import_task_modules()
    hook = get_status_hook(task.kind)
    if hook is not None:
        await hook(session, task, status)


async def _run_success_hook(session: Any, task: Task, outputs: dict[str, Any]) -> None:
    """Fire the ``on_success`` hook registered for ``task.kind``, if any."""
    _import_task_modules()
    hook = get_success_hook(task.kind)
    if hook is not None:
        await hook(session, task, outputs)


# Dependency-readiness vocabulary is single-sourced in
# ``sfmapi.server.orchestrator.readiness`` (shared with the scheduler and the
# janitor); ``_dependency_state_from_statuses`` is its
# ``dependency_state`` imported at the top of this module under the
# historical local name.


async def _task_dependency_state(session: Any, task: Task) -> str:
    deps = [str(dep) for dep in (task.depends_on_json or [])]
    if not deps:
        return "ready"
    rows = (await session.execute(select(Task).where(Task.task_id.in_(deps)))).scalars().all()
    status_by_id = {row.task_id: row.status for row in rows}
    return _dependency_state_from_statuses(deps, status_by_id)


async def _mark_dependency_failures_and_ready(session: Any, job_id: str) -> list[str]:
    rows = (await session.execute(select(Task).where(Task.job_id == job_id))).scalars().all()
    status_by_id = {task.task_id: task.status for task in rows}
    changed = True
    while changed:
        changed = False
        for task in rows:
            if task.status != "pending":
                continue
            deps = [str(dep) for dep in (task.depends_on_json or [])]
            if not deps:
                continue
            dep_state = _dependency_state_from_statuses(deps, status_by_id)
            if dep_state == "failed":
                task.status = "failed"
                task.error_class = "DependencyFailed"
                task.error_message = "upstream dependency failed"
                task.finished_at = now_utc()
                status_by_id[task.task_id] = task.status
                changed = True
            elif dep_state in CANCELLED_DEPENDENCY_STATUSES:
                task.status = dep_state
                task.error_class = "DependencyCancelled"
                task.error_message = "upstream dependency cancelled"
                task.finished_at = now_utc()
                status_by_id[task.task_id] = task.status
                changed = True
    ready: list[str] = []
    for task in rows:
        if task.status != "pending":
            continue
        deps = [str(dep) for dep in (task.depends_on_json or [])]
        if deps and all(status_by_id.get(dep) in READY_DEPENDENCY_STATUSES for dep in deps):
            ready.append(task.task_id)
    return ready


async def _enqueue_task_ids(task_ids: list[str]) -> None:
    if not task_ids:
        return
    # Shared, process-cached queue — closing it per task completion
    # would rebuild the Redis pool on every DAG advancement.
    queue = get_shared_queue()
    for task_id in task_ids:
        with contextlib.suppress(Exception):
            await queue.enqueue(task_id)


async def _advance_job_after_terminal(job_id: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        ready = await _mark_dependency_failures_and_ready(session, job_id)
        await job_service.finalize_job_if_ready(session, job_id=job_id)
        await session.commit()
    await _enqueue_task_ids(ready)


async def _finalize_task(
    session: Any,
    task: Task,
    *,
    status: str,
    error_class: str | None = None,
    error_message: str | None = None,
) -> None:
    """Apply one non-success terminal transition and commit it.

    Sets the terminal ``status`` (plus the error fields when given and
    ``finished_at``), fires the kind's ``on_status`` hook with the
    resource status ``"failed"`` (cancelled tasks also fail their
    resource), and commits the session. Callers await
    :func:`_post_terminal` right after for the job-level roll-ups.
    """
    task.status = status
    if error_class is not None:
        task.error_class = error_class
    if error_message is not None:
        task.error_message = error_message
    task.finished_at = now_utc()
    await _run_status_hook(session, task, "failed")
    await session.commit()


async def _post_terminal(session: Any, job_id: str) -> None:
    """Job-level roll-ups after a committed terminal task transition:
    finalize the job if every task is terminal, then advance the DAG.

    ``_maybe_finalize_job`` reuses the caller's (already committed)
    session, exactly as the pre-extraction inline blocks did;
    ``_advance_job_after_terminal`` opens its own.
    """
    await _maybe_finalize_job(session, job_id)
    await _advance_job_after_terminal(job_id)


async def execute_task(task_id: str) -> dict[str, Any]:
    """Run one Task end-to-end. Queue-agnostic: any backend that can
    deliver a ``task_id`` can call this.

    Returns one of:
      ``{"status": "missing"}``  — task row gone.
      ``{"status": "busy"}``     — another worker holds the lease.
      ``{"status": "cancelled"}``— job cancel was requested before pickup.
      ``{"status": "succeeded", "outputs": ...}``
      ``{"status": "failed", "error": ...}``

    Cancellation is cooperative and checked at task pickup and before task
    success is committed: if the parent
    ``Job.cancel_requested`` flag is set before this worker acquires the
    lease, the task short-circuits to ``cancelled`` (or ``cancelled_dirty``
    when ``cancel_force`` is set) and never runs the handler. If the flag is
    set while the handler is running, outputs are not committed and the task
    lands as ``cancelled_dirty`` because external side effects may already
    exist. Immediate subprocess termination is a per-handler concern. A job whose
    every task already hit the cache (``succeeded`` in ``materialize_dag``)
    cannot be cancelled — there is nothing left to short-circuit.
    """
    log = get_logger("worker.execute_task").bind(task_id=task_id, worker_id=WORKER_ID)
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Task).where(Task.task_id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            return {"status": "missing"}
        if task.status != "pending":
            await session.commit()
            log.info("task.not_pending", status=task.status)
            return {"status": task.status}
        job = await session.get(Job, task.job_id)
        project_id = job.project_id if job is not None else None
        dep_state = await _task_dependency_state(session, task)
        if dep_state == "blocked":
            await session.commit()
            log.info("task.dependencies_blocked")
            return {"status": "blocked"}
        if dep_state == "failed":
            task.status = "failed"
            task.error_class = "DependencyFailed"
            task.error_message = "upstream dependency failed"
            task.finished_at = now_utc()
            await session.commit()
            await _advance_job_after_terminal(task.job_id)
            log.info("task.dependency_failed")
            return {"status": "failed", "error": "dependency_failed"}
        if dep_state in CANCELLED_DEPENDENCY_STATUSES:
            task.status = dep_state
            task.error_class = "DependencyCancelled"
            task.error_message = "upstream dependency cancelled"
            task.finished_at = now_utc()
            await session.commit()
            await _advance_job_after_terminal(task.job_id)
            log.info("task.dependency_cancelled")
            return {"status": dep_state}
        acquired = await try_acquire_lease(
            session,
            table=Task.__table__,
            pk_col=Task.task_id,
            lease_col=Task.lease_expires_at,
            worker_col=Task.worker_id,
            pk_value=task_id,
            worker_id=WORKER_ID,
            ttl_seconds=settings.lease_ttl_seconds,
            extra_where=(Task.status == "pending",),
        )
        if not acquired:
            await session.commit()
            log.info("task.lease_busy")
            return {"status": "busy"}
        await session.refresh(task)
        job = await session.get(Job, task.job_id, populate_existing=True)
        # Cooperative cancellation: the lease gate above guarantees a single
        # owner, so finalizing here can't race another worker. ``cancel_force``
        # maps to ``cancelled_dirty`` (the task may have left partial state
        # behind on a prior run); a plain request maps to ``cancelled``.
        if job is not None and job.cancel_requested:
            await _finalize_task(
                session,
                task,
                status="cancelled_dirty" if job.cancel_force else "cancelled",
            )
            await _post_terminal(session, task.job_id)
            log.info("task.cancelled", force=job.cancel_force)
            return {"status": "cancelled"}
        task.status = "running"
        task.started_at = now_utc()
        await _run_status_hook(session, task, "running")
        await session.commit()

    handler = get_handlers().get(task.kind)
    if handler is None:
        async with factory() as session:
            t = await session.get(Task, task_id)
            if t is None:
                return {"status": "missing"}
            if not _owns_live_running_lease(t):
                log.info("task.lost_lease_before_unknown_handler", status=t.status)
                return {"status": "lost_lease"}
            await _finalize_task(
                session,
                t,
                status="failed",
                error_class="UnknownTask",
                error_message=f"No handler for kind={task.kind}",
            )
            await _post_terminal(session, t.job_id)
        return {"status": "failed"}

    event_path = None
    if project_id is not None:
        event_path = (
            Paths(settings).job_root(task.tenant_id, project_id, task.job_id) / "events.jsonl"
        )
    reporter = WorkerProgressReporter(
        job_id=task.job_id,
        task_id=task.task_id,
        loop=asyncio.get_running_loop(),
        event_path=event_path,
    )
    heartbeat_task = asyncio.create_task(_heartbeat(task_id))
    try:
        token = set_progress_reporter(reporter)
        try:
            raw_outputs = await asyncio.to_thread(handler, task)
            outputs = artifact_service.normalize_task_outputs(task, raw_outputs)
        finally:
            reset_progress_reporter(token)
        async with factory() as session:
            t = await session.get(Task, task_id)
            if t is None:
                return {"status": "missing"}
            if not _owns_live_running_lease(t):
                log.info("task.lost_lease_before_success_commit", status=t.status)
                return {"status": "lost_lease"}
            j = await session.get(Job, t.job_id)
            if j is not None and j.cancel_requested:
                await _finalize_task(
                    session,
                    t,
                    status="cancelled_dirty",
                    error_class="Cancelled",
                    error_message="job cancellation requested before task commit",
                )
                await _post_terminal(session, t.job_id)
                log.info("task.cancelled_after_handler", force=j.cancel_force)
                return {"status": "cancelled_dirty"}
            await dataset_service.register_derived_dataset(session, task=t, outputs=outputs or {})
            t.status = "succeeded"
            t.outputs_ref_json = outputs or {}
            t.finished_at = now_utc()
            await artifact_service.record_task_artifacts(session, task=t, outputs=outputs or {})
            await _run_success_hook(session, t, outputs or {})
            await session.commit()
            await _post_terminal(session, t.job_id)
        return {"status": "succeeded", "outputs": outputs}
    except BackendUnavailableError as e:
        # Engine-neutral: any backend that can't load its engine lands
        # here. ``error_class`` derives from the exception type so the
        # deprecated ``PycolmapUnavailableError`` subclass keeps its
        # serialized legacy wire string ("PycolmapUnavailable").
        async with factory() as session:
            t = await session.get(Task, task_id)
            if t is None:
                return {"status": "missing"}
            if not _owns_live_running_lease(t):
                log.info("task.lost_lease_before_backend_unavailable_failure", status=t.status)
                return {"status": "lost_lease"}
            await _finalize_task(
                session,
                t,
                status="failed",
                error_class=type(e).__name__.removesuffix("Error"),
                error_message=sanitize_public_error_message(e),
            )
            await _post_terminal(session, t.job_id)
        return {"status": "failed", "error": "backend_unavailable"}
    except Exception as e:
        public_error = sanitize_public_error_message(e)
        log.error("task.failed", err=public_error, error_class=type(e).__name__)
        log.debug("task.failed.traceback", exc_info=True)
        async with factory() as session:
            t = await session.get(Task, task_id)
            if t is None:
                return {"status": "missing"}
            if not _owns_live_running_lease(t):
                log.info("task.lost_lease_before_failure_commit", status=t.status)
                return {"status": "lost_lease"}
            await _finalize_task(
                session,
                t,
                status="failed",
                error_class=type(e).__name__,
                error_message=public_error,
            )
            await _post_terminal(session, t.job_id)
        return {"status": "failed", "error": public_error}
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
            refreshed = await refresh_lease(
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
            if not refreshed:
                return
