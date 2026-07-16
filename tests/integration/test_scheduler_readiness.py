"""Submit-time dependency readiness (shared-vocabulary regression).

Before the readiness vocabulary was single-sourced in
``app.orchestrator.readiness``, the scheduler counted only
``succeeded`` dependencies as satisfied and only looked at the tasks
materialized in the current submission. A task depending on an
upstream that already landed as ``skipped`` (or any pre-existing task
from an earlier job) was therefore never enqueued at submit time and
sat ``pending`` until a janitor sweep. These tests pin the fix.
"""

from __future__ import annotations

import pytest

from app.core.ids import new_id
from app.db.models import Job, Project, Task
from app.orchestrator.dag import TaskNode
from app.orchestrator.scheduler import submit_job_dag

pytestmark = pytest.mark.integration


async def _seed_upstream_task(session, *, status: str) -> str:
    """Persist a task from a *previous* job with the given status."""
    project = Project(tenant_id="default", name=f"sched-dep-{new_id()[-10:]}")
    session.add(project)
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="external", status=status)
    session.add(job)
    await session.flush()
    task = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        inputs_hash="u",
        params_hash="u",
        runtime_version_id="rv",
        cache_key=new_id(),
        gpu_required=False,
        status=status,
        task_state_json={"inputs": {}, "spec": {}},
    )
    session.add(task)
    await session.commit()
    return task.task_id


async def _submit_single_dependent_node(session, *, dep_task_id: str) -> str:
    project = Project(tenant_id="default", name=f"sched-sub-{new_id()[-10:]}")
    session.add(project)
    await session.flush()
    node = TaskNode(
        task_id=new_id(),
        kind="noop",
        inputs_hash=new_id(),  # unique — never a cache hit
        params_hash=new_id(),
        depends_on=[dep_task_id],
        gpu_required=False,
    )
    _job_id, tasks = await submit_job_dag(
        session,
        tenant_id="default",
        project_id=project.project_id,
        recipe="noop",
        spec={},
        nodes=[node],
        inline=True,
    )
    await session.commit()
    return tasks[0].task_id


async def test_submit_enqueues_task_whose_dependency_is_already_skipped(session) -> None:
    """The regression: a ``skipped`` upstream satisfies the dependency at
    submit time — the task must run immediately (inline queue), not wait
    for the janitor sweep."""
    dep_id = await _seed_upstream_task(session, status="skipped")

    task_id = await _submit_single_dependent_node(session, dep_task_id=dep_id)

    task = await session.get(Task, task_id)
    await session.refresh(task)
    assert task.status == "succeeded"


async def test_submit_enqueues_task_with_cross_job_succeeded_dependency(session) -> None:
    """Cross-job dependency statuses are resolved from the DB, not just
    from the tasks materialized in the current submission."""
    dep_id = await _seed_upstream_task(session, status="succeeded")

    task_id = await _submit_single_dependent_node(session, dep_task_id=dep_id)

    task = await session.get(Task, task_id)
    await session.refresh(task)
    assert task.status == "succeeded"


async def test_submit_leaves_task_pending_when_dependency_not_terminal(session) -> None:
    """A non-reusable upstream (still running) must keep the downstream
    pending at submit — janitor/dispatcher advancement picks it up later."""
    dep_id = await _seed_upstream_task(session, status="running")

    task_id = await _submit_single_dependent_node(session, dep_task_id=dep_id)

    task = await session.get(Task, task_id)
    await session.refresh(task)
    assert task.status == "pending"
