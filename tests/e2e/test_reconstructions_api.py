from __future__ import annotations

import pytest

from sceneapi.server.core.hashing import canonical_json, content_address
from sceneapi.server.core.ids import new_id
from sceneapi.server.db.models import (
    Job,
    Reconstruction,
    RuntimeVersion,
    StageArtifact,
    SubModel,
    Task,
)
from sceneapi.server.storage.snapshots import SnapshotStore

pytestmark = pytest.mark.e2e


async def _seed_recon(session, tmp_workspace) -> tuple[str, str, str]:
    rv = RuntimeVersion(
        rv_id=new_id(),
        runtime_version_id="test-rv",
        seed="0",
    )
    session.add(rv)
    await session.flush()

    from sceneapi.server.db.models import Dataset, ImageSource, Project

    p = Project(tenant_id="default", name="recon-p")
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
    )
    session.add(d)
    await session.flush()

    spec = {"kind": "incremental", "version": 1}
    r = Reconstruction(
        tenant_id="default",
        project_id=p.project_id,
        dataset_id=d.dataset_id,
        dataset_snapshot_hash=content_address(canonical_json({"images": []})),
        spec_json=spec,
        rv_id=rv.rv_id,
        status="succeeded",
    )
    session.add(r)
    await session.flush()
    return p.project_id, d.dataset_id, r.recon_id


async def test_get_reconstruction_and_list_submodels(client, session, tmp_path) -> None:
    _pid, _did, rid = await _seed_recon(session, tmp_path)
    sm = SubModel(
        tenant_id="default",
        recon_id=rid,
        idx=0,
        summary_json={"num_reg_images": 4, "num_points3D": 100},
        snapshot_seq=1,
        sealed_path="/tmp/snapshot/0",
    )
    session.add(sm)
    await session.commit()

    resp = await client.get(f"/v1/reconstructions/{rid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recon_id"] == rid
    assert body["status"] == "succeeded"

    list_resp = await client.get(f"/v1/reconstructions/{rid}/submodels")
    assert list_resp.status_code == 200
    page = list_resp.json()
    assert len(page["items"]) == 1
    assert page["items"][0]["summary"]["num_reg_images"] == 4
    assert page["items"][0]["sealed_path"] is None
    assert "/tmp/snapshot" not in repr(page)
    assert page["items"][0]["_links"]["self"]["href"].startswith("/v1/submodels/")
    assert (
        page["items"][0]["_links"]["cameras"]["href"]
        == f"/v1/reconstructions/{rid}/snapshots/1/submodels/0/cameras.json"
    )


async def test_snapshot_endpoints_serve_sealed_files(client, session) -> None:
    from sceneapi.server.core.config import get_settings
    from sceneapi.server.core.paths import Paths

    pid, _did, rid = await _seed_recon(session, None)
    await session.commit()

    s = get_settings()
    paths = Paths(s)
    rec_root = paths.reconstruction_root("default", pid, rid)
    rec_root.mkdir(parents=True, exist_ok=True)
    src = rec_root / "live"
    src.mkdir()
    (src / "cameras.json").write_text('{"cameras":[{"id":1}]}')

    store = SnapshotStore(rec_root)
    store.seal(seq=1, source_dir=src, summary={"phase": "test"})

    seqs = await client.get(f"/v1/reconstructions/{rid}/snapshots")
    assert seqs.status_code == 200
    assert seqs.json()["seqs"] == [1]

    cams = await client.get(f"/v1/reconstructions/{rid}/snapshots/1/cameras.json")
    assert cams.status_code == 200
    assert cams.headers["content-type"].startswith("application/json")
    assert cams.json() == {"cameras": [{"id": 1}]}

    summ = await client.get(f"/v1/reconstructions/{rid}/snapshots/1/summary.json")
    assert summ.status_code == 200
    assert summ.json()["phase"] == "test"

    missing = await client.get(f"/v1/reconstructions/{rid}/snapshots/1/does_not_exist.json")
    assert missing.status_code == 404


async def test_submodel_snapshot_endpoint_serves_component_files(client, session) -> None:
    from sceneapi.server.core.config import get_settings
    from sceneapi.server.core.paths import Paths

    pid, _did, rid = await _seed_recon(session, None)
    await session.commit()

    paths = Paths(get_settings())
    rec_root = paths.reconstruction_root("default", pid, rid)
    rec_root.mkdir(parents=True, exist_ok=True)
    src = rec_root / "live"
    (src / "0").mkdir(parents=True)
    (src / "1").mkdir(parents=True)
    (src / "0" / "cameras.json").write_text('{"component":0}', encoding="utf-8")
    (src / "1" / "cameras.json").write_text('{"component":1}', encoding="utf-8")

    store = SnapshotStore(rec_root)
    store.seal(seq=1, source_dir=src, summary={"models": [{"idx": 0}, {"idx": 1}]})

    component = await client.get(f"/v1/reconstructions/{rid}/snapshots/1/submodels/1/cameras.json")
    assert component.status_code == 200
    assert component.json() == {"component": 1}

    missing = await client.get(f"/v1/reconstructions/{rid}/snapshots/1/submodels/2/cameras.json")
    assert missing.status_code == 404


async def test_artifact_endpoints_list_typed_outputs(client, session) -> None:
    from sceneapi.server.core.config import get_settings

    pid, did, rid = await _seed_recon(session, None)
    job = Job(tenant_id="default", project_id=pid, recipe="test", status="succeeded")
    session.add(job)
    await session.flush()
    task = Task(
        tenant_id="default",
        job_id=job.job_id,
        kind="match",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        status="succeeded",
    )
    session.add(task)
    await session.flush()
    artifact_file = get_settings().workspace_root / "artifacts" / "two_view_geometries.json"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text('{"pairs":[]}', encoding="utf-8")
    artifact = StageArtifact(
        tenant_id="default",
        job_id=job.job_id,
        task_id=task.task_id,
        recon_id=rid,
        dataset_id=did,
        kind="matches.verified.v1",
        name="hloc-lightglue-verified",
        uri=str(artifact_file),
        media_type="application/json",
        summary_json={"num_verified_pairs": 12},
    )
    session.add(artifact)
    await session.commit()

    by_job = await client.get(f"/v1/jobs/{job.job_id}/artifacts")
    assert by_job.status_code == 200
    assert by_job.json()["items"][0]["kind"] == "matches.verified.v1"
    assert by_job.json()["items"][0]["_links"]["self"]["href"] == (
        f"/v1/artifacts/{artifact.artifact_id}"
    )

    filtered = await client.get(
        f"/v1/jobs/{job.job_id}/artifacts",
        params={"kind": "reconstruction.snapshot"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []

    by_recon = await client.get(f"/v1/reconstructions/{rid}/artifacts")
    assert by_recon.status_code == 200
    item = by_recon.json()["items"][0]
    assert item["name"] == "hloc-lightglue-verified"
    assert item["summary"]["num_verified_pairs"] == 12
    assert item["_links"]["reconstruction"]["href"] == f"/v1/reconstructions/{rid}"

    kinds = await client.get("/v1/artifacts/kinds")
    assert kinds.status_code == 200
    assert "matches.verified.v1" in {row["kind"] for row in kinds.json()["items"]}

    detail = await client.get(f"/v1/artifacts/{artifact.artifact_id}")
    assert detail.status_code == 200
    assert detail.json()["artifact_id"] == artifact.artifact_id

    content = await client.get(f"/v1/artifacts/{artifact.artifact_id}/content")
    assert content.status_code == 200
    assert content.json() == {"pairs": []}
