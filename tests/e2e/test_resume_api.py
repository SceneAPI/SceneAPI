from __future__ import annotations

import pytest

from sceneapi.server.core.ids import new_id
from sceneapi.server.db.models import Job, Project, RuntimeVersion, Task

pytestmark = pytest.mark.e2e


async def _seed_failed_job(session) -> str:
    rv = RuntimeVersion(
        rv_id=new_id(),
        runtime_version_id="test-rv",
        seed="0",
    )
    p = Project(tenant_id="default", name="resume-api-p")
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
    bad = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=j.job_id,
        kind="noop",
        inputs_hash="i",
        params_hash="p",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        status="failed",
    )
    session.add(bad)
    await session.commit()
    return j.job_id


async def test_resume_endpoint_runs_pending_inline(client, session) -> None:
    job_id = await _seed_failed_job(session)
    resp = await client.post(f"/v1/jobs/{job_id}:resume")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    detail = await client.get(f"/v1/jobs/{job_id}")
    statuses = [t["status"] for t in detail.json()["tasks"]]
    assert "succeeded" in statuses
