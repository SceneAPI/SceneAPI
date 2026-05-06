from __future__ import annotations

import pytest

from app.core.ids import new_id
from app.db.models import Job, Project, RuntimeVersion, Task
from app.orchestrator.resume import resume_job

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
    assert resumed.status == "pending"
