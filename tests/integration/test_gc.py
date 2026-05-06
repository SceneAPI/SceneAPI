from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.ids import new_id
from app.core.paths import Paths
from app.db.models import Job, Project, Reconstruction, RuntimeVersion
from app.storage.workspace import gc_completed_jobs

pytestmark = pytest.mark.integration


async def test_gc_drops_dense_then_snapshots_keeps_pinned(session) -> None:
    rv = RuntimeVersion(
        rv_id=new_id(),
        runtime_version_id="test-rv",
        seed="0",
    )
    p = Project(tenant_id="default", name="gc-p")
    session.add_all([rv, p])
    await session.flush()

    long_ago = datetime.now(UTC) - timedelta(days=30)
    j_old = Job(
        tenant_id="default",
        project_id=p.project_id,
        recipe="incremental",
        status="succeeded",
        finished_at=long_ago,
    )
    j_pinned = Job(
        tenant_id="default",
        project_id=p.project_id,
        recipe="incremental",
        status="succeeded",
        finished_at=long_ago,
        pinned=True,
    )
    session.add_all([j_old, j_pinned])
    await session.flush()

    r_old = Reconstruction(
        tenant_id="default",
        project_id=p.project_id,
        dataset_id=new_id(),
        dataset_snapshot_hash="x",
        spec_json={},
        rv_id=rv.rv_id,
    )
    r_pin = Reconstruction(
        tenant_id="default",
        project_id=p.project_id,
        dataset_id=new_id(),
        dataset_snapshot_hash="y",
        spec_json={},
        rv_id=rv.rv_id,
    )
    session.add_all([r_old, r_pin])
    await session.commit()

    paths = Paths()
    job_dir_old = paths.job_root("default", p.project_id, j_old.job_id)
    job_dir_pin = paths.job_root("default", p.project_id, j_pinned.job_id)
    for jd in (job_dir_old, job_dir_pin):
        (jd / "dense").mkdir(parents=True, exist_ok=True)
        (jd / "dense" / "p.ply").write_text("k")
        (jd / "snapshots" / "00000001").mkdir(parents=True, exist_ok=True)
        (jd / "snapshots" / "00000001" / "x.txt").write_text("k")
        (jd / "sparse" / "0").mkdir(parents=True, exist_ok=True)
        (jd / "sparse" / "0" / "x.txt").write_text("k")

    summary = await gc_completed_jobs(session, ttl_days=7)
    assert summary["considered"] == 1
    assert summary["dense_dropped"] == 1
    assert summary["snapshots_dropped"] == 1
    assert summary["sparse_dropped"] == 1
    assert (job_dir_old / "dense").exists() is False
    assert (job_dir_old / "snapshots").exists() is False
    assert (job_dir_old / "sparse").exists() is False
    # Pinned job untouched at every step.
    assert (job_dir_pin / "dense" / "p.ply").is_file()
    assert (job_dir_pin / "snapshots" / "00000001" / "x.txt").is_file()
    assert (job_dir_pin / "sparse" / "0" / "x.txt").is_file()
