"""Job CRUD + cache lookup + DAG persistence.

The orchestrator owns DAG building (in `sfmapi.server.orchestrator.dag`). This
service persists Jobs and Tasks to the DB and is responsible for cache
short-circuit logic.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.core.errors import NotFoundError
from sfmapi.server.core.ids import new_id
from sfmapi.server.db.models import Job, Task
from sfmapi.server.db.pagination import paginate_keyset
from sfmapi.server.orchestrator.dag import TaskNode
from sfmapi.server.orchestrator.lease import now_utc
from sfmapi.server.services import artifact_service


async def create_job(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    recipe: str,
    spec: dict[str, Any] | None,
) -> Job:
    j = Job(
        tenant_id=tenant_id,
        project_id=project_id,
        recipe=recipe,
        spec_json=spec,
        status="pending",
    )
    session.add(j)
    await session.flush()
    return j


async def get_job(session: AsyncSession, *, tenant_id: str, job_id: str) -> Job:
    result = await session.execute(
        select(Job).where(Job.tenant_id == tenant_id, Job.job_id == job_id)
    )
    j = result.scalar_one_or_none()
    if j is None:
        raise NotFoundError(f"Job {job_id} not found")
    return j


async def list_jobs(
    session: AsyncSession,
    *,
    tenant_id: str,
    page_size: int = 50,
    page_token: str | None = None,
    status: str | None = None,
) -> tuple[list[Job], str | None]:
    """AIP-158 keyset pagination on ``job_id`` descending (most recent
    submissions first). ``status`` filters to a single lifecycle state
    when set — the canonical cheap-filter exposed in lieu of a full
    AIP-160 grammar."""
    stmt = select(Job).where(Job.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    return await paginate_keyset(
        session,
        stmt,
        pk=Job.job_id,
        page_size=page_size,
        page_token=page_token,
        descending=True,
    )


async def lookup_cached_task(
    session: AsyncSession, *, tenant_id: str, cache_key: str
) -> Task | None:
    result = await session.execute(
        select(Task)
        .where(
            Task.tenant_id == tenant_id,
            Task.cache_key == cache_key,
            Task.status == "succeeded",
        )
        .order_by(Task.finished_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def materialize_dag(
    session: AsyncSession,
    *,
    tenant_id: str,
    job_id: str,
    runtime_version_id: str,
    nodes: Iterable[TaskNode],
) -> list[Task]:
    """Persist task nodes to the DB. Looks up cache hits per task; if a
    successful Task with the same `cache_key` exists, copies its
    `outputs_ref_json` and marks the new Task `succeeded` immediately."""
    out: list[Task] = []
    cached_tasks: list[Task] = []
    for n in nodes:
        if not n.task_id:
            n.task_id = new_id()
        ck = n.cache_key(runtime_version_id)
        cached = await lookup_cached_task(session, tenant_id=tenant_id, cache_key=ck)
        t = Task(
            task_id=n.task_id,
            tenant_id=tenant_id,
            job_id=job_id,
            kind=n.kind,
            inputs_hash=n.inputs_hash,
            params_hash=n.params_hash,
            runtime_version_id=runtime_version_id,
            cache_key=ck,
            depends_on_json=list(n.depends_on),
            gpu_required=n.gpu_required,
        )
        if n.metadata:
            # Persist ``inputs`` / ``spec`` so the worker handler can
            # read them via ``sfmapi.server.workers._task_io.read_state``. Stored
            # in ``task_state_json`` (pre-execution carrier); the
            # dispatcher writes the actual result to ``outputs_ref_json``
            # post-execution. See ``L27`` in ``decisions.md`` for the
            # split rationale. Cached tasks still need this state so
            # inferred StageArtifact rows keep the new job's resource
            # pointers.
            t.task_state_json = dict(n.metadata)
        if cached is not None:
            t.status = "succeeded"
            t.outputs_ref_json = cached.outputs_ref_json
            cached_tasks.append(t)
        session.add(t)
        out.append(t)
    await session.flush()
    for t in cached_tasks:
        outputs = artifact_service.normalize_task_outputs(t, t.outputs_ref_json or {})
        t.outputs_ref_json = outputs
        await artifact_service.record_task_artifacts(session, task=t, outputs=outputs)
    return out


async def finalize_job_if_ready(session: AsyncSession, *, job_id: str) -> Job | None:
    """Roll up a job once every task is terminal.

    Used both by the worker and by submit-time cache hits. The function is
    idempotent and does not commit; callers own transaction boundaries.
    """
    rows = (await session.execute(select(Task).where(Task.job_id == job_id))).scalars().all()
    if not rows:
        return None
    statuses = {t.status for t in rows}
    if statuses & {"pending", "running"}:
        return None
    if "failed" in statuses:
        new_status = "failed"
    elif statuses & {"cancelled", "cancelled_dirty"}:
        new_status = "cancelled"
    else:
        new_status = "succeeded"
    job = await session.get(Job, job_id)
    if job is None:
        return None
    if job.status != new_status:
        job.status = new_status
        job.finished_at = now_utc()
    if new_status == "failed":
        for task in rows:
            if task.status == "failed":
                job.error_class = task.error_class
                job.error_message = task.error_message
                break
    return job


async def cancel_job(session: AsyncSession, *, tenant_id: str, job_id: str, force: bool) -> Job:
    j = await get_job(session, tenant_id=tenant_id, job_id=job_id)
    j.cancel_requested = True
    j.cancel_force = bool(force) or j.cancel_force
    return j
