"""Drive the live ephemeral server through a representative request
sequence and persist each response to ``fixtures/<name>.json``.

These fixtures are the input for the SDK round-trip tests in this
directory. Adding a new fixture is the canonical way to broaden
contract coverage to a new endpoint or response shape.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.contract.conftest import save_fixture

pytestmark = pytest.mark.contract


async def test_record_health(contract_client: AsyncClient) -> None:
    r = await contract_client.get("/healthz")
    assert r.status_code == 200
    save_fixture("healthz", r.json())


async def test_record_version(contract_client: AsyncClient) -> None:
    r = await contract_client.get("/version")
    assert r.status_code == 200
    save_fixture("version", r.json())


async def test_record_capabilities(contract_client: AsyncClient) -> None:
    r = await contract_client.get("/v1/capabilities")
    assert r.status_code == 200
    body = r.json()
    # Confirm the new schema_version field is present.
    assert "schema_version" in body
    assert "backend" in body
    assert "features" in body
    save_fixture("capabilities", body)


async def test_record_spec(contract_client: AsyncClient) -> None:
    r = await contract_client.get("/spec")
    assert r.status_code == 200
    save_fixture("spec", r.json())


async def test_record_project_lifecycle(contract_client: AsyncClient) -> None:
    create = await contract_client.post(
        "/v1/projects", json={"name": "contract-demo", "description": "fixture"}
    )
    assert create.status_code in (200, 201), create.text
    project = create.json()
    save_fixture("project_create", project)

    pid = project["project_id"]
    get_one = await contract_client.get(f"/v1/projects/{pid}")
    assert get_one.status_code == 200
    save_fixture("project_get", get_one.json())

    list_resp = await contract_client.get("/v1/projects")
    assert list_resp.status_code == 200
    save_fixture("project_list", list_resp.json())


async def test_record_dataset_create(contract_client: AsyncClient) -> None:
    p = await contract_client.post("/v1/projects", json={"name": "ds-host"})
    pid = p.json()["project_id"]
    r = await contract_client.post(
        f"/v1/projects/{pid}/datasets",
        json={
            "name": "empty",
            "source": {"kind": "upload", "entries": []},
            "camera_model": "SIMPLE_RADIAL",
            "intrinsics_mode": "single_camera",
            "is_spherical": False,
            "respect_exif_orientation": False,
        },
    )
    assert r.status_code in (200, 201), r.text
    save_fixture("dataset_create", r.json())


async def test_record_404_error_envelope(contract_client: AsyncClient) -> None:
    """Captures the RFC7807 problem+json shape so SDKs decoding error
    bodies can validate against a real example."""
    r = await contract_client.get("/v1/projects/01HZNOTAREALPROJID00000000")
    assert r.status_code == 404
    save_fixture("error_404_project_missing", r.json())


async def test_record_422_validation_envelope(contract_client: AsyncClient) -> None:
    """Captures FastAPI's Pydantic validation error shape (`detail` is
    a list of error dicts)."""
    r = await contract_client.post("/v1/projects", json={"description": "no name"})
    assert r.status_code == 422
    save_fixture("error_422_validation", r.json())


async def test_record_empty_pagination(contract_client: AsyncClient) -> None:
    """A list endpoint with no rows still returns the canonical Page
    envelope — capture it so SDKs can assert the shape on no-data."""
    p = await contract_client.post("/v1/projects", json={"name": "for-empty-list"})
    pid = p.json()["project_id"]
    r = await contract_client.get(f"/v1/projects/{pid}/datasets")
    assert r.status_code == 200
    body = r.json()
    assert body.get("items") == []
    save_fixture("page_empty", body)


async def test_record_upload_init(contract_client: AsyncClient) -> None:
    """Records the chunked-upload init response (Upload envelope).
    Recording a full upload+finalize+image cycle would require real
    bytes flowing through; init alone is enough to lock the wire
    shape of the Upload resource."""
    r = await contract_client.post(
        "/v1/uploads", json={"expected_size": 1024, "content_type": "image/jpeg"}
    )
    assert r.status_code in (200, 201), r.text
    save_fixture("upload_init", r.json())


async def _stub_uploaded_blob(client: AsyncClient) -> str:
    """Upload a few bytes through the chunked-upload protocol so a
    real Blob row exists. Returns the resulting sha. Used by stage
    fixtures that need an addressable image."""
    init = await client.post(
        "/v1/uploads",
        json={"expected_size": 4, "content_type": "application/octet-stream"},
    )
    assert init.status_code in (200, 201), init.text
    upload_id = init.json()["upload_id"]
    patch = await client.patch(
        f"/v1/uploads/{upload_id}",
        content=b"\x00\x01\x02\x03",
        headers={"Content-Range": "bytes 0-3/4", "Content-Type": "application/octet-stream"},
    )
    assert patch.status_code in (200, 204), patch.text
    fin = await client.post(f"/v1/uploads/{upload_id}:finalize")
    assert fin.status_code in (200, 201), fin.text
    return fin.json()["blob_sha"]


async def test_record_features_submit_envelope(contract_client: AsyncClient) -> None:
    """``POST /v1/datasets/{id}/features`` returns the canonical
    202 ``JobAcceptedResponse`` envelope. Lock its shape into a
    fixture so all three SDKs (Python hand-rolled + generated,
    TS hand-rolled + generated, C++) can validate decoders."""
    proj = (await contract_client.post("/v1/projects", json={"name": "stage-host"})).json()
    pid = proj["project_id"]
    ds = (
        await contract_client.post(
            f"/v1/projects/{pid}/datasets",
            json={
                "name": "stage-ds",
                "source": {"kind": "upload", "entries": []},
                "camera_model": "SIMPLE_RADIAL",
                "intrinsics_mode": "single_camera",
                "is_spherical": False,
                "respect_exif_orientation": False,
            },
        )
    ).json()
    did = ds["dataset_id"]
    # Register a stub image so the stage validation passes; the
    # worker will fail trying to read the missing blob, but the
    # 202 wire shape is what we're capturing.
    sha = await _stub_uploaded_blob(contract_client)
    img = await contract_client.post(
        f"/v1/datasets/{did}/images",
        json={"name": "stub.jpg", "blob_sha": sha, "width": 100, "height": 100},
    )
    assert img.status_code in (200, 201), img.text
    submit = await contract_client.post(
        f"/v1/datasets/{did}/features",
        json={"spec": {"version": 1, "type": "sift", "max_num_features": 1024}},
    )
    assert submit.status_code in (200, 201, 202), submit.text
    save_fixture("job_accepted_features", submit.json())


async def test_record_merge_submit_envelope(contract_client: AsyncClient) -> None:
    """``POST /v1/reconstructions:merge`` returns the same canonical
    202 ``JobAcceptedResponse`` envelope as the SfM stages, but with the
    merge-specific fields (``target_recon_id`` / ``source_recon_ids``)
    populated. Locking it as a fixture gives the SDK decoders a
    provider-carrying surface beyond the single-stage shape."""
    from app.adapters.registry import register_backend
    from app.adapters.stub_backend import StubBackend
    from app.core.capabilities import reset_capabilities_cache

    class MergeCapableBackend(StubBackend):
        def capabilities(self) -> set[str]:
            return {"recon.merge", "map.incremental"}

    register_backend("stub", MergeCapableBackend)
    reset_capabilities_cache()

    proj = (await contract_client.post("/v1/projects", json={"name": "merge-host"})).json()
    pid = proj["project_id"]
    ds = (
        await contract_client.post(
            f"/v1/projects/{pid}/datasets",
            json={
                "name": "merge-ds",
                "source": {"kind": "upload", "entries": []},
                "camera_model": "SIMPLE_PINHOLE",
                "intrinsics_mode": "single_camera",
                "is_spherical": False,
                "respect_exif_orientation": False,
            },
        )
    ).json()
    did = ds["dataset_id"]
    sha = await _stub_uploaded_blob(contract_client)
    await contract_client.post(
        f"/v1/datasets/{did}/images",
        json={"name": "stub.jpg", "blob_sha": sha, "width": 100, "height": 100},
    )

    # Each pipeline submit allocates a reconstruction row up front; the
    # worker run fails on the stub but the recon_id is what we need.
    recon_ids: list[str] = []
    for seed in (1, 2):
        submit = await contract_client.post(
            f"/v1/projects/{pid}/pipelines/incremental",
            json={"dataset_id": did, "spec": {"kind": "incremental", "version": 1, "seed": seed}},
        )
        assert submit.status_code in (200, 201, 202), submit.text
        recon_ids.append(submit.json()["recon_id"])

    merge = await contract_client.post(
        "/v1/reconstructions:merge",
        json={"target_recon_id": recon_ids[0], "source_recon_ids": [recon_ids[1]]},
    )
    assert merge.status_code in (200, 201, 202), merge.text
    body = merge.json()
    assert body["target_recon_id"] == recon_ids[0]
    assert body["source_recon_ids"] == [recon_ids[1]]
    assert "provider" in body  # the field is on the wire even when null
    save_fixture("job_accepted_merge", body)


async def test_record_snapshot_list_empty(contract_client: AsyncClient) -> None:
    """``GET /v1/reconstructions/{id}/snapshots`` on a brand-new
    reconstruction returns an empty ``SnapshotListResponse``. The
    spherical pipelines route is the easiest way to create one
    without running real SfM."""
    proj = (await contract_client.post("/v1/projects", json={"name": "snap-host"})).json()
    pid = proj["project_id"]
    ds = (
        await contract_client.post(
            f"/v1/projects/{pid}/datasets",
            json={
                "name": "snap-ds",
                "source": {"kind": "upload", "entries": []},
                "camera_model": "SIMPLE_PINHOLE",
                "intrinsics_mode": "single_camera",
                "is_spherical": False,
                "respect_exif_orientation": False,
            },
        )
    ).json()
    did = ds["dataset_id"]
    sha = await _stub_uploaded_blob(contract_client)
    img = await contract_client.post(
        f"/v1/datasets/{did}/images",
        json={"name": "stub.jpg", "blob_sha": sha, "width": 100, "height": 100},
    )
    assert img.status_code in (200, 201), img.text
    # Submit a pipeline recipe — its 202 envelope returns the recon_id
    # we need to address the snapshots endpoint. The worker run will
    # fail (no real pycolmap) but the recon row is allocated upfront.
    submit = await contract_client.post(
        f"/v1/projects/{pid}/pipelines/incremental",
        json={
            "dataset_id": did,
            "spec": {"kind": "incremental", "version": 1},
        },
    )
    if submit.status_code not in (200, 201, 202):
        return
    rid = submit.json().get("recon_id")
    if not rid:
        return
    snaps = await contract_client.get(f"/v1/reconstructions/{rid}/snapshots")
    assert snaps.status_code == 200, snaps.text
    save_fixture("snapshot_list_empty", snaps.json())


async def test_record_404_on_missing_dataset(contract_client: AsyncClient) -> None:
    """A 404 from a nested resource path. Datasets are addressed via
    ``/v1/projects/{pid}/datasets/{did}`` — there is no top-level
    ``/v1/datasets/{did}`` GET, so the dataset_id alone is not
    sufficient. Useful to confirm the problem-json shape is consistent
    across resource kinds."""
    p = await contract_client.post("/v1/projects", json={"name": "missing-ds-host"})
    pid = p.json()["project_id"]
    r = await contract_client.get(f"/v1/projects/{pid}/datasets/01HZNOTAREALDATASETID00000")
    assert r.status_code == 404
    save_fixture("error_404_dataset_missing", r.json())
