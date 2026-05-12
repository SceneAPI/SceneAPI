"""Inline runner test — submits a no-op task DAG and runs it synchronously."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.hashing import canonical_json, content_address
from app.core.ids import new_id
from app.db.models import (
    Dataset,
    ImageSource,
    Project,
    Reconstruction,
    StageArtifact,
    SubModel,
    Task,
)
from app.orchestrator.dag import TaskNode
from app.orchestrator.scheduler import submit_job_dag

pytestmark = pytest.mark.integration


async def test_noop_task_runs_and_succeeds(session) -> None:
    p = Project(tenant_id="default", name="t-runner")
    session.add(p)
    await session.flush()

    node = TaskNode(
        task_id=new_id(),
        kind="noop",
        inputs_hash="i",
        params_hash="p",
        depends_on=[],
        gpu_required=False,
    )
    _job_id, tasks = await submit_job_dag(
        session,
        tenant_id="default",
        project_id=p.project_id,
        recipe="noop",
        spec={},
        nodes=[node],
        inline=True,
    )
    await session.commit()
    t = await session.get(Task, tasks[0].task_id)
    await session.refresh(t)
    assert t.status == "succeeded"
    assert t.outputs_ref_json["ok"] is True


async def test_cache_short_circuit(session) -> None:
    p = Project(tenant_id="default", name="t-cache")
    session.add(p)
    await session.flush()

    def make_node() -> TaskNode:
        return TaskNode(
            task_id=new_id(),
            kind="noop",
            inputs_hash="ih",
            params_hash="ph",
            depends_on=[],
            gpu_required=False,
        )

    _, tasks_a = await submit_job_dag(
        session,
        tenant_id="default",
        project_id=p.project_id,
        recipe="noop",
        spec={},
        nodes=[make_node()],
        inline=True,
    )
    a_id = tasks_a[0].task_id
    a = await session.get(Task, a_id)
    await session.refresh(a)
    assert a.status == "succeeded"

    _, tasks_b = await submit_job_dag(
        session,
        tenant_id="default",
        project_id=p.project_id,
        recipe="noop",
        spec={},
        nodes=[make_node()],
        inline=True,
    )
    b_id = tasks_b[0].task_id
    b = await session.get(Task, b_id)
    await session.refresh(b)
    # Same cache_key -> second task starts as 'succeeded' (cached).
    assert b.status == "succeeded"
    assert b.outputs_ref_json == a.outputs_ref_json
    assert b.cache_key == a.cache_key


async def test_map_task_persists_submodels(session, monkeypatch) -> None:
    from app.workers import dispatcher

    p = Project(tenant_id="default", name="t-map-submodels")
    session.add(p)
    await session.flush()
    src = ImageSource(tenant_id="default", kind="upload", fingerprint_json={})
    session.add(src)
    await session.flush()
    d = Dataset(
        tenant_id="default",
        project_id=p.project_id,
        source_id=src.source_id,
        name="ds",
        manifest_hash=content_address(canonical_json({"images": ["a.jpg", "b.jpg"]})),
    )
    session.add(d)
    await session.flush()
    r = Reconstruction(
        tenant_id="default",
        project_id=p.project_id,
        dataset_id=d.dataset_id,
        dataset_snapshot_hash=d.manifest_hash,
        spec_json={"kind": "incremental", "version": 1},
        rv_id="test-rv",
    )
    session.add(r)
    await session.flush()

    snapshot_path = str(Path("C:/tmp/recon/snapshots/00000003"))

    def fake_map(_task: Task) -> dict:
        return {
            "snapshot_seq": 3,
            "snapshot_path": snapshot_path,
            "models": [
                {"idx": 1, "num_reg_images": 8, "num_points3D": 100},
                {"idx": 0, "num_reg_images": 12, "num_points3D": 300},
            ],
            "artifacts": [
                {
                    "kind": "reconstruction.snapshot",
                    "name": "snapshot-3",
                    "uri": snapshot_path,
                    "artifact_format": "reconstruction.snapshot",
                    "schema_version": 1,
                },
                {
                    "kind": "reconstruction.submodel",
                    "name": "submodel-0",
                    "uri": str(Path(snapshot_path) / "0"),
                    "artifact_format": "reconstruction.submodel",
                    "schema_version": 1,
                },
                {
                    "kind": "reconstruction.submodel",
                    "name": "submodel-1",
                    "uri": str(Path(snapshot_path) / "1"),
                    "artifact_format": "reconstruction.submodel",
                    "schema_version": 1,
                },
            ],
        }

    monkeypatch.setattr(dispatcher, "_HANDLERS_CACHE", {"map": fake_map})
    node = TaskNode(
        task_id=new_id(),
        kind="map",
        inputs_hash="map-inputs",
        params_hash="map-params",
        depends_on=[],
        gpu_required=False,
        metadata={
            "inputs": {
                "project_id": p.project_id,
                "dataset_id": d.dataset_id,
                "recon_id": r.recon_id,
            },
            "spec": {"kind": "incremental", "version": 1},
        },
    )
    _job_id, tasks = await submit_job_dag(
        session,
        tenant_id="default",
        project_id=p.project_id,
        recipe="incremental",
        spec={},
        nodes=[node],
        inline=True,
    )
    await session.commit()

    task = await session.get(Task, tasks[0].task_id)
    await session.refresh(task)
    await session.refresh(r)
    rows = (
        (
            await session.execute(
                select(SubModel).where(SubModel.recon_id == r.recon_id).order_by(SubModel.idx)
            )
        )
        .scalars()
        .all()
    )

    assert task.status == "succeeded"
    assert r.status == "succeeded"
    assert [row.idx for row in rows] == [0, 1]
    assert [row.summary_json["num_reg_images"] for row in rows] == [12, 8]
    assert rows[0].snapshot_seq == 3
    assert rows[0].sealed_path == str(Path(snapshot_path) / "0")
    assert rows[1].sealed_path == str(Path(snapshot_path) / "1")

    artifacts = (
        (
            await session.execute(
                select(StageArtifact)
                .where(StageArtifact.task_id == task.task_id)
                .order_by(StageArtifact.kind, StageArtifact.name)
            )
        )
        .scalars()
        .all()
    )
    assert [artifact.kind for artifact in artifacts] == [
        "reconstruction.snapshot",
        "reconstruction.submodel",
        "reconstruction.submodel",
    ]
    assert {artifact.name for artifact in artifacts} == {
        "snapshot-3",
        "submodel-0",
        "submodel-1",
    }
