from __future__ import annotations

import asyncio

import pytest

from sceneapi.server.core.ids import new_id
from sceneapi.server.db.models import RuntimeVersion, Task

pytestmark = pytest.mark.integration


async def _seed_task(session) -> str:
    rv = RuntimeVersion(
        rv_id=new_id(),
        runtime_version_id="test-rv",
        seed="0",
    )
    session.add(rv)
    await session.flush()
    # Need a parent job: skip job FK by inserting a dummy job via SQL
    from sceneapi.server.db.models import Job, Project

    p = Project(tenant_id="default", name="leasep")
    session.add(p)
    await session.flush()
    j = Job(tenant_id="default", project_id=p.project_id, recipe="x")
    session.add(j)
    await session.flush()
    t = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=j.job_id,
        kind="noop",
        inputs_hash="x",
        params_hash="x",
        runtime_version_id=rv.rv_id,
        cache_key="x",
        gpu_required=True,
    )
    session.add(t)
    await session.commit()
    return t.task_id


async def test_lease_acquire_blocks_second_worker(session) -> None:
    from sceneapi.server.orchestrator.lease import try_acquire_lease

    tid = await _seed_task(session)
    a = await try_acquire_lease(
        session,
        table=Task.__table__,
        pk_col=Task.task_id,
        lease_col=Task.lease_expires_at,
        worker_col=Task.worker_id,
        pk_value=tid,
        worker_id="worker-A",
        ttl_seconds=30,
    )
    await session.commit()
    assert a is True
    b = await try_acquire_lease(
        session,
        table=Task.__table__,
        pk_col=Task.task_id,
        lease_col=Task.lease_expires_at,
        worker_col=Task.worker_id,
        pk_value=tid,
        worker_id="worker-B",
        ttl_seconds=30,
    )
    await session.commit()
    assert b is False


async def test_lease_reclaimable_after_expiry(session) -> None:
    from sceneapi.server.orchestrator.lease import try_acquire_lease

    tid = await _seed_task(session)
    await try_acquire_lease(
        session,
        table=Task.__table__,
        pk_col=Task.task_id,
        lease_col=Task.lease_expires_at,
        worker_col=Task.worker_id,
        pk_value=tid,
        worker_id="worker-A",
        ttl_seconds=0,  # already expired
    )
    await session.commit()
    await asyncio.sleep(0.01)
    b = await try_acquire_lease(
        session,
        table=Task.__table__,
        pk_col=Task.task_id,
        lease_col=Task.lease_expires_at,
        worker_col=Task.worker_id,
        pk_value=tid,
        worker_id="worker-B",
        ttl_seconds=30,
    )
    await session.commit()
    assert b is True
