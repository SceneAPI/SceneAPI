"""Scheduler — submit job DAG and enqueue tasks for execution.

Persists Job + Task rows and hands each non-cached task off to the
configured queue (see ``app.orchestrator.queue.get_queue``). Whether
the queue is ARQ-backed or in-process is a runtime decision driven by
``settings.queue_backend`` / ``settings.inline_tasks``.
"""

from __future__ import annotations

import contextlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.ids import new_id
from app.db.models import Task
from app.orchestrator.dag import TaskNode
from app.orchestrator.queue import InlineQueue, force_inline_queue, get_queue, reset_inline_queue
from app.services import job_service, runtime_version_service


def _dependency_ready(task: Task, status_by_id: dict[str, str]) -> bool:
    deps = list(task.depends_on_json or [])
    return all(status_by_id.get(str(dep)) == "succeeded" for dep in deps)


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
    ready = [t for t in pending if _dependency_ready(t, status_by_id)]

    inline_token = force_inline_queue() if inline else None
    queue = InlineQueue(get_settings()) if inline else get_queue()
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
        await queue.close()
        if inline_token is not None:
            reset_inline_queue(inline_token)
    return job.job_id, tasks
