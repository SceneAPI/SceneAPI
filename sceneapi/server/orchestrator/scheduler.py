"""Scheduler — submit job DAG and enqueue tasks for execution.

Persists Job + Task rows and hands each non-cached task off to the
configured queue (see ``sceneapi.server.orchestrator.queue.get_shared_queue``).
Whether the queue is ARQ-backed or in-process is a runtime decision
driven by ``settings.queue_backend`` / ``settings.inline_tasks``.
"""

from __future__ import annotations

import contextlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.core.config import get_settings
from sceneapi.server.core.ids import new_id
from sceneapi.server.db.models import Task
from sceneapi.server.orchestrator.dag import TaskNode
from sceneapi.server.orchestrator.queue import (
    InlineQueue,
    force_inline_queue,
    get_shared_queue,
    reset_inline_queue,
)
from sceneapi.server.orchestrator.readiness import dependencies_ready
from sceneapi.server.services import job_service, runtime_version_service


async def _resolve_external_dep_statuses(
    session: AsyncSession, tasks: list[Task], status_by_id: dict[str, str]
) -> None:
    """Merge DB statuses for dependencies outside this submission.

    DAG edges may point at pre-existing Task rows (cross-job deps, or
    rows written by external workers as ``skipped``); those aren't in
    the freshly materialized set, so look them up. Deps missing from
    the DB stay absent from ``status_by_id`` (= not ready)."""
    external = {
        str(dep) for t in tasks for dep in (t.depends_on_json or []) if str(dep) not in status_by_id
    }
    if not external:
        return
    rows = await session.execute(
        select(Task.task_id, Task.status).where(Task.task_id.in_(external))
    )
    status_by_id.update({str(task_id): str(status) for task_id, status in rows.all()})


async def submit_job_dag(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    recipe: str,
    spec: dict | None,
    nodes: list[TaskNode],
    inline: bool = False,
) -> tuple[str, list[Task]]:
    """Persist Job + Task rows and return them. ``inline=True`` forces
    the InlineQueue regardless of settings (used by tests). ARQ enqueue
    failures (e.g. Redis absent in dev) are suppressed; the tasks remain
    ``pending`` and are retried by the ready-pending janitor sweep."""
    rv = await runtime_version_service.ensure_runtime_version(session)
    job = await job_service.create_job(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        recipe=recipe,
        spec=spec,
    )
    for n in nodes:
        if not n.task_id:
            n.task_id = new_id()
    tasks = await job_service.materialize_dag(
        session,
        tenant_id=tenant_id,
        job_id=job.job_id,
        runtime_version_id=rv.rv_id,
        nodes=nodes,
    )
    await session.commit()

    status_by_id = {t.task_id: t.status for t in tasks}
    pending = [t for t in tasks if t.status != "succeeded"]
    if not pending:
        await job_service.finalize_job_if_ready(session, job_id=job.job_id)
        await session.commit()
        return job.job_id, tasks
    await _resolve_external_dep_statuses(session, pending, status_by_id)
    ready = [t for t in pending if dependencies_ready(t.depends_on_json or [], status_by_id)]

    inline_token = force_inline_queue() if inline else None
    # The shared queue is process-cached — do NOT close it per submit.
    queue = InlineQueue(get_settings()) if inline else get_shared_queue()
    try:
        for t in ready:
            # Inline mode surfaces task errors directly; ARQ enqueue
            # failures are tolerated (Redis may be absent in dev).
            if isinstance(queue, InlineQueue):
                await queue.enqueue(t.task_id)
            else:
                with contextlib.suppress(Exception):
                    await queue.enqueue(t.task_id)
    finally:
        if inline_token is not None:
            reset_inline_queue(inline_token)
    return job.job_id, tasks
