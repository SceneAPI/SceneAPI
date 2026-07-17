from __future__ import annotations

import pytest
from sqlalchemy import select

from sceneapi.server.core.errors import ValidationError
from sceneapi.server.core.ids import new_id
from sceneapi.server.db.models import Job, Project, RuntimeVersion, Task
from sceneapi.server.orchestrator.resume import resume_job

pytestmark = pytest.mark.integration


async def test_resume_resets_failed_tasks_only(session) -> None:
    rv = RuntimeVersion(
        rv_id=new_id(),
        runtime_version_id="test-rv",
        seed="0",
    )
    p = Project(tenant_id="default", name="resume-p")
    session.add_all([rv, p])
    await session.flush()
    j = Job(
        tenant_id="default",
        project_id=p.project_id,
        recipe="incremental",
        status="failed",
        error_class="X",
        error_message="boom",
        started_at=p.created_at,
        finished_at=p.created_at,
    )
    session.add(j)
    await session.flush()
    t_ok = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=j.job_id,
        kind="extract",
        inputs_hash="i",
        params_hash="p",
        runtime_version_id=rv.rv_id,
        cache_key="ck-ok",
        status="succeeded",
    )
    t_bad = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=j.job_id,
        kind="noop",
        inputs_hash="i",
        params_hash="p",
        runtime_version_id=rv.rv_id,
        cache_key="ck-bad",
        status="failed",
        error_class="X",
        error_message="boom",
        started_at=p.created_at,
        finished_at=p.created_at,
    )
    session.add_all([t_ok, t_bad])
    await session.commit()

    # inline=False so the test only validates the reset semantics, not
    # the re-execution path (which is covered by test_resume_api).
    resumed = await resume_job(session, tenant_id="default", job_id=j.job_id, inline=False)
    await session.commit()

    fresh_ok = await session.get(Task, t_ok.task_id)
    fresh_bad = await session.get(Task, t_bad.task_id)
    assert fresh_ok.status == "succeeded"
    assert fresh_bad.status == "pending"
    assert fresh_bad.error_class is None
    assert fresh_bad.started_at is None
    assert fresh_bad.finished_at is None
    assert resumed.status == "pending"
    assert resumed.started_at is None
    assert resumed.finished_at is None


async def test_resume_enqueues_only_dependency_ready_tasks(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    p = Project(tenant_id="default", name="resume-ready-p")
    session.add_all([rv, p])
    await session.flush()
    j = Job(
        tenant_id="default",
        project_id=p.project_id,
        recipe="dag",
        status="failed",
    )
    session.add(j)
    await session.flush()
    first = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=j.job_id,
        kind="noop",
        inputs_hash="a",
        params_hash="a",
        runtime_version_id=rv.rv_id,
        cache_key="ck-a",
        status="failed",
    )
    second = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=j.job_id,
        kind="noop",
        inputs_hash="b",
        params_hash="b",
        runtime_version_id=rv.rv_id,
        cache_key="ck-b",
        status="failed",
        depends_on_json=[first.task_id],
    )
    session.add_all([first, second])
    await session.commit()

    enqueued: list[str] = []

    class FakeQueue:
        async def enqueue(self, task_id: str) -> None:
            enqueued.append(task_id)

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        "sceneapi.server.orchestrator.queue.get_queue",
        lambda *_args, **_kwargs: FakeQueue(),
    )

    await resume_job(session, tenant_id="default", job_id=j.job_id, inline=False)

    assert enqueued == [first.task_id]


async def test_resume_rejects_non_resumable_job_status(session) -> None:
    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    p = Project(tenant_id="default", name="resume-not-terminal-p")
    session.add_all([rv, p])
    await session.flush()
    j = Job(
        tenant_id="default",
        project_id=p.project_id,
        recipe="done",
        status="succeeded",
    )
    session.add(j)
    await session.flush()
    session.add(
        Task(
            task_id=new_id(),
            tenant_id="default",
            job_id=j.job_id,
            kind="noop",
            inputs_hash="i",
            params_hash="p",
            runtime_version_id=rv.rv_id,
            cache_key="ck",
            status="succeeded",
        )
    )
    await session.commit()

    with pytest.raises(ValidationError, match="not resumable"):
        await resume_job(session, tenant_id="default", job_id=j.job_id, inline=False)


async def test_resume_rejects_failed_job_with_no_resettable_tasks(session) -> None:
    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    p = Project(tenant_id="default", name="resume-no-reset-p")
    session.add_all([rv, p])
    await session.flush()
    j = Job(
        tenant_id="default",
        project_id=p.project_id,
        recipe="malformed",
        status="failed",
    )
    session.add(j)
    await session.flush()
    session.add(
        Task(
            task_id=new_id(),
            tenant_id="default",
            job_id=j.job_id,
            kind="noop",
            inputs_hash="i",
            params_hash="p",
            runtime_version_id=rv.rv_id,
            cache_key="ck",
            status="succeeded",
        )
    )
    await session.commit()

    with pytest.raises(ValidationError, match="no failed or cancelled tasks"):
        await resume_job(session, tenant_id="default", job_id=j.job_id, inline=False)


async def test_inline_resume_forces_downstream_dag_work_inline(session) -> None:
    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    p = Project(tenant_id="default", name="resume-inline-dag-p")
    session.add_all([rv, p])
    await session.flush()
    j = Job(tenant_id="default", project_id=p.project_id, recipe="dag", status="failed")
    session.add(j)
    await session.flush()
    first = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=j.job_id,
        kind="noop",
        inputs_hash="a",
        params_hash="a",
        runtime_version_id=rv.rv_id,
        cache_key="ck-a",
        status="failed",
    )
    second = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=j.job_id,
        kind="noop",
        inputs_hash="b",
        params_hash="b",
        runtime_version_id=rv.rv_id,
        cache_key="ck-b",
        status="failed",
        depends_on_json=[first.task_id],
    )
    session.add_all([first, second])
    await session.commit()

    await resume_job(session, tenant_id="default", job_id=j.job_id, inline=True)

    rows = (
        (
            await session.execute(
                select(Task).where(Task.job_id == j.job_id).order_by(Task.created_at)
            )
        )
        .scalars()
        .all()
    )
    fresh_job = await session.get(Job, j.job_id)
    assert [row.status for row in rows] == ["succeeded", "succeeded"]
    assert fresh_job.status == "succeeded"
