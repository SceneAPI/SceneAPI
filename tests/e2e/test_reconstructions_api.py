from __future__ import annotations

import pytest

from app.core.hashing import canonical_json, content_address
from app.core.ids import new_id
from app.db.models import Reconstruction, RuntimeVersion, SubModel
from app.storage.snapshots import SnapshotStore

pytestmark = pytest.mark.e2e


async def _seed_recon(session, tmp_workspace) -> tuple[str, str, str]:
    rv = RuntimeVersion(
        rv_id=new_id(),
        runtime_version_id="test-rv",
        seed="0",
    )
    session.add(rv)
    await session.flush()

    from app.db.models import Dataset, ImageSource, Project

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
    assert page["items"][0]["_links"]["self"]["href"].startswith("/v1/submodels/")


async def test_snapshot_endpoints_serve_sealed_files(client, session) -> None:
    from app.core.config import get_settings
    from app.core.paths import Paths

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
