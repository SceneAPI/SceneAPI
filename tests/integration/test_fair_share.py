from __future__ import annotations

import pytest

from app.core.ids import new_id
from app.db.models import Job, Project, RuntimeVersion, Task
from app.orchestrator.fair_share import FairShareState, pick_next_task

pytestmark = pytest.mark.integration


async def _seed_project_jobs_for(session, *, tenant_id: str, n: int) -> tuple[str, list[Task]]:
    rv = (
        await session.execute(__import__("sqlalchemy").select(RuntimeVersion))
    ).scalar_one_or_none()
    if rv is None:
        rv = RuntimeVersion(
            rv_id=new_id(),
            runtime_version_id="test-rv",
            seed="0",
        )
        session.add(rv)
        await session.flush()
    p = Project(tenant_id=tenant_id, name=f"p-{tenant_id}")
    session.add(p)
    await session.flush()
    j = Job(tenant_id=tenant_id, project_id=p.project_id, recipe="incremental")
    session.add(j)
    await session.flush()
    tasks: list[Task] = []
    for _ in range(n):
        t = Task(
            task_id=new_id(),
            tenant_id=tenant_id,
            job_id=j.job_id,
            kind="noop",
            inputs_hash=new_id(),
            params_hash="p",
            runtime_version_id=rv.rv_id,
            cache_key=new_id(),
            gpu_required=False,
        )
        session.add(t)
        tasks.append(t)
    await session.commit()
    return j.job_id, tasks


async def test_max_consecutive_per_tenant_respected(session) -> None:
    await _seed_project_jobs_for(session, tenant_id="t-A", n=10)
    await _seed_project_jobs_for(session, tenant_id="t-B", n=10)
    state = FairShareState(max_consecutive_per_tenant=2)
    picked: list[str] = []
    for _ in range(20):
        t = await pick_next_task(session, state=state)
        if t is None:
            break
        t.status = "running"  # pretend we admitted/started it
        await session.commit()
        picked.append(t.tenant_id)
    # No run of >2 consecutive should appear when the other tenant has work.
    for i in range(len(picked) - 2):
        triple = picked[i : i + 3]
        assert not (triple[0] == triple[1] == triple[2]), picked


async def test_returns_none_when_no_pending(session) -> None:
    state = FairShareState()
    assert await pick_next_task(session, state=state) is None
