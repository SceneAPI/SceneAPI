"""Cooperative job cancellation at task pickup.

``POST /v1/jobs/{id}:cancel`` flips ``Job.cancel_requested`` /
``cancel_force``; the dispatcher honors them when it picks up a task.
Before this wiring those flags were set but never read — a cancelled
job ran to completion. These tests pin the short-circuit path.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from sfmapi.server.core.ids import new_id
from sfmapi.server.db.models import Job, Project, RuntimeVersion, Task

pytestmark = pytest.mark.integration


async def _seed_cancelled_job(session, *, force: bool) -> tuple[str, str]:
    """Persist a Project + a cancel-requested Job + one pending Task.

    Returns ``(job_id, task_id)``.
    """
    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name="cancelp")
    session.add(project)
    await session.flush()
    job = Job(
        tenant_id="default",
        project_id=project.project_id,
        recipe="x",
        cancel_requested=True,
        cancel_force=force,
    )
    session.add(job)
    await session.flush()
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
    )
    session.add(task)
    await session.commit()
    return job.job_id, task.task_id


async def _seed_cancelled_dag(session, *, force: bool) -> tuple[str, str, str]:
    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name="cancel-dag")
    session.add(project)
    await session.flush()
    job = Job(
        tenant_id="default",
        project_id=project.project_id,
        recipe="x",
        cancel_requested=True,
        cancel_force=force,
    )
    session.add(job)
    await session.flush()
    first = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        inputs_hash="a",
        params_hash="a",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
    )
    second = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        inputs_hash="b",
        params_hash="b",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
        depends_on_json=[first.task_id],
    )
    session.add_all([first, second])
    await session.commit()
    return job.job_id, first.task_id, second.task_id


async def test_cancel_requested_short_circuits_task_to_cancelled(session) -> None:
    from sfmapi.server.workers.dispatcher import execute_task

    job_id, task_id = await _seed_cancelled_job(session, force=False)

    result = await execute_task(task_id)
    assert result == {"status": "cancelled"}

    refreshed_task = await session.get(Task, task_id)
    await session.refresh(refreshed_task)
    assert refreshed_task.status == "cancelled"
    assert refreshed_task.finished_at is not None

    refreshed_job = await session.get(Job, job_id)
    await session.refresh(refreshed_job)
    assert refreshed_job.status == "cancelled"
    assert refreshed_job.finished_at is not None


async def test_cancelled_dependency_rolls_dag_up_to_cancelled(session) -> None:
    from sfmapi.server.workers.dispatcher import execute_task

    job_id, first_id, second_id = await _seed_cancelled_dag(session, force=False)

    result = await execute_task(first_id)
    assert result == {"status": "cancelled"}

    first = await session.get(Task, first_id)
    second = await session.get(Task, second_id)
    job = await session.get(Job, job_id)
    await session.refresh(first)
    await session.refresh(second)
    await session.refresh(job)

    assert first.status == "cancelled"
    assert second.status == "cancelled"
    assert second.error_class == "DependencyCancelled"
    assert job.status == "cancelled"


async def test_cancel_force_marks_task_cancelled_dirty(session) -> None:
    from sfmapi.server.workers.dispatcher import execute_task

    job_id, task_id = await _seed_cancelled_job(session, force=True)

    result = await execute_task(task_id)
    assert result == {"status": "cancelled"}

    refreshed_task = await session.get(Task, task_id)
    await session.refresh(refreshed_task)
    assert refreshed_task.status == "cancelled_dirty"

    refreshed_job = await session.get(Job, job_id)
    await session.refresh(refreshed_job)
    # _maybe_finalize_job rolls cancelled_dirty up to the cancelled job state.
    assert refreshed_job.status == "cancelled"


async def test_uncancelled_job_runs_the_task_normally(session) -> None:
    """Guard the negative: a job with no cancel flag is not short-circuited
    — the noop handler runs and the task reaches ``succeeded``."""
    from sfmapi.server.workers.dispatcher import execute_task

    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name="okp")
    session.add(project)
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="x")
    session.add(job)
    await session.flush()
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
    )
    session.add(task)
    await session.commit()

    result = await execute_task(task.task_id)
    assert result["status"] == "succeeded"

    refreshed_task = await session.get(Task, task.task_id)
    await session.refresh(refreshed_task)
    assert refreshed_task.status == "succeeded"


async def test_cancel_requested_while_handler_runs_prevents_success_commit(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel arriving after pickup still wins before outputs commit."""
    from sfmapi.server.workers import dispatcher
    from sfmapi.server.workers.dispatcher import execute_task

    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    project = Project(tenant_id="default", name="mid-handler-cancel")
    session.add_all([rv, project])
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="x")
    session.add(job)
    await session.flush()
    task = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="slow_cancel",
        inputs_hash="x",
        params_hash="x",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
    )
    session.add(task)
    await session.commit()

    def slow_handler(_task: Task) -> dict[str, bool]:
        time.sleep(0.2)
        return {"ok": True}

    monkeypatch.setattr(dispatcher, "_HANDLERS_CACHE", {"slow_cancel": slow_handler})

    runner = asyncio.create_task(execute_task(task.task_id))
    for _ in range(100):
        await asyncio.sleep(0.01)
        refreshed = await session.get(Task, task.task_id)
        await session.refresh(refreshed)
        if refreshed.status == "running":
            break
    else:
        pytest.fail("task did not enter running state")

    fresh_job = await session.get(Job, job.job_id)
    fresh_job.cancel_requested = True
    await session.commit()

    result = await runner
    assert result == {"status": "cancelled_dirty"}

    refreshed_task = await session.get(Task, task.task_id)
    await session.refresh(refreshed_task)
    assert refreshed_task.status == "cancelled_dirty"
    assert refreshed_task.outputs_ref_json is None

    refreshed_job = await session.get(Job, job.job_id)
    await session.refresh(refreshed_job)
    assert refreshed_job.status == "cancelled"


async def test_cancel_between_read_and_lease_short_circuits_handler(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel committed after the first task read but before lease
    acquisition must be observed before the handler starts."""
    from sfmapi.server.workers import dispatcher
    from sfmapi.server.workers.dispatcher import execute_task

    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    project = Project(tenant_id="default", name="pre-lease-cancel")
    session.add_all([rv, project])
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="x")
    session.add(job)
    await session.flush()
    task = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="should_not_run",
        inputs_hash="x",
        params_hash="x",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
    )
    session.add(task)
    await session.commit()

    called = False

    def handler(_task: Task) -> dict[str, bool]:
        nonlocal called
        called = True
        return {"ok": True}

    original_acquire = dispatcher.try_acquire_lease

    async def cancelling_acquire(*args, **kwargs):
        acquire_session = kwargs.get("session") or args[0]
        fresh_job = await acquire_session.get(Job, job.job_id, populate_existing=True)
        fresh_job.cancel_requested = True
        await acquire_session.flush()
        return await original_acquire(*args, **kwargs)

    monkeypatch.setattr(dispatcher, "_HANDLERS_CACHE", {"should_not_run": handler})
    monkeypatch.setattr(dispatcher, "try_acquire_lease", cancelling_acquire)

    result = await execute_task(task.task_id)
    assert result == {"status": "cancelled"}
    assert called is False

    refreshed_task = await session.get(Task, task.task_id)
    await session.refresh(refreshed_task)
    assert refreshed_task.status == "cancelled"


async def test_lost_lease_prevents_success_commit(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the janitor or another worker takes the lease, the original worker
    must not commit success outputs after its handler returns."""
    from sfmapi.server.db.session import get_session_factory
    from sfmapi.server.workers import dispatcher
    from sfmapi.server.workers.dispatcher import execute_task

    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    project = Project(tenant_id="default", name="lost-lease")
    session.add_all([rv, project])
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="x")
    session.add(job)
    await session.flush()
    task = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="loses_lease",
        inputs_hash="x",
        params_hash="x",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
    )
    session.add(task)
    await session.commit()

    def handler(_task: Task) -> dict[str, bool]:
        async def clear_lease() -> None:
            factory = get_session_factory()
            async with factory() as other:
                stolen = await other.get(Task, task.task_id)
                stolen.status = "pending"
                stolen.worker_id = None
                stolen.lease_expires_at = None
                await other.commit()

        asyncio.run(clear_lease())
        return {"ok": True}

    monkeypatch.setattr(dispatcher, "_HANDLERS_CACHE", {"loses_lease": handler})

    result = await execute_task(task.task_id)
    assert result == {"status": "lost_lease"}

    refreshed_task = await session.get(Task, task.task_id)
    await session.refresh(refreshed_task)
    assert refreshed_task.status == "pending"
    assert refreshed_task.outputs_ref_json is None

    refreshed_job = await session.get(Job, job.job_id)
    await session.refresh(refreshed_job)
    assert refreshed_job.status == "pending"
