"""Lease-reclaim janitor.

A task whose worker dies keeps its ``running`` status forever — the
heartbeat stops, the lease ages out, but nothing resets it. The janitor
sweeps for those expired leases. These tests pin the reclaim predicate
and the re-enqueue side effect.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.ids import new_id
from app.db.models import Job, Project, RuntimeVersion, Task, Upload
from app.orchestrator.lease import now_utc

pytestmark = pytest.mark.integration


async def _seed_task(
    session,
    *,
    status: str,
    lease_offset_seconds: float | None,
) -> str:
    """Persist a Task with a given status and lease age.

    ``lease_offset_seconds`` is relative to now: negative = expired,
    positive = still valid, ``None`` = never leased.
    """
    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name=f"janp-{new_id()[:6]}")
    session.add(project)
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="x")
    session.add(job)
    await session.flush()
    lease = None
    worker = None
    if lease_offset_seconds is not None:
        lease = now_utc() + timedelta(seconds=lease_offset_seconds)
        worker = "worker-A"
    task = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        inputs_hash="x",
        params_hash="x",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
        status=status,
        worker_id=worker,
        lease_expires_at=lease,
    )
    session.add(task)
    await session.commit()
    return task.task_id


async def test_reclaim_resets_running_task_with_expired_lease(session) -> None:
    from app.orchestrator.janitor import reclaim_expired_leases

    task_id = await _seed_task(session, status="running", lease_offset_seconds=-60)

    reclaimed = await reclaim_expired_leases(session)
    assert reclaimed == [task_id]

    task = await session.get(Task, task_id)
    await session.refresh(task)
    assert task.status == "pending"
    assert task.worker_id is None
    assert task.lease_expires_at is None


async def test_reclaim_ignores_running_task_with_valid_lease(session) -> None:
    from app.orchestrator.janitor import reclaim_expired_leases

    task_id = await _seed_task(session, status="running", lease_offset_seconds=60)

    reclaimed = await reclaim_expired_leases(session)
    assert reclaimed == []

    task = await session.get(Task, task_id)
    await session.refresh(task)
    assert task.status == "running"
    assert task.worker_id == "worker-A"


async def test_reclaim_ignores_terminal_task_with_expired_lease(session) -> None:
    """A succeeded task with a stale lease must not be dragged back to
    pending — only ``running`` tasks are reclaimable."""
    from app.orchestrator.janitor import reclaim_expired_leases

    task_id = await _seed_task(session, status="succeeded", lease_offset_seconds=-60)

    reclaimed = await reclaim_expired_leases(session)
    assert reclaimed == []

    task = await session.get(Task, task_id)
    await session.refresh(task)
    assert task.status == "succeeded"


async def test_run_janitor_once_re_enqueues_reclaimed_task(session) -> None:
    """``run_janitor_once`` resets the lease AND re-enqueues — under the
    inline queue the noop task runs and reaches ``succeeded``."""
    from app.orchestrator.janitor import run_janitor_once

    task_id = await _seed_task(session, status="running", lease_offset_seconds=-60)

    reclaimed = await run_janitor_once(session)
    assert reclaimed == [task_id]

    task = await session.get(Task, task_id)
    await session.refresh(task)
    # InlineQueue.enqueue runs the task synchronously to a terminal state.
    assert task.status == "succeeded"


async def _seed_upload(session, *, state: str, expires_offset_seconds: float) -> str:
    upload = Upload(
        upload_id=new_id(),
        tenant_id="default",
        expected_size=1024,
        received_bytes=0,
        state=state,
        expires_at=now_utc() + timedelta(seconds=expires_offset_seconds),
    )
    session.add(upload)
    await session.commit()
    return upload.upload_id


async def test_run_janitor_once_reaps_expired_unfinalized_upload(session) -> None:
    """The janitor sweep also drops uploads past expires_at that were
    never finalized — this is what backs the UploadState 'expired ...
    reaped by the janitor' doc claim."""
    from app.orchestrator.janitor import run_janitor_once

    stale = await _seed_upload(session, state="open", expires_offset_seconds=-3600)
    fresh = await _seed_upload(session, state="open", expires_offset_seconds=3600)
    finalized = await _seed_upload(session, state="finalized", expires_offset_seconds=-3600)

    await run_janitor_once(session)

    assert await session.get(Upload, stale) is None
    assert await session.get(Upload, fresh) is not None
    # A finalized upload past expires_at is kept — its bytes are content
    # addressed and may still be referenced.
    assert await session.get(Upload, finalized) is not None
