"""Dataset CRUD symmetry — every POSTed resource MUST be deletable.

Covers the dataset DELETE that was missing for several spec
revisions — guards against a regression that drops the route again."""

from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage

pytestmark = pytest.mark.conformance


def _jpeg() -> bytes:
    im = PILImage.new("RGB", (8, 8), color=(20, 40, 60))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


async def _upload(client, payload: bytes) -> str:
    init = await client.post("/v1/uploads", json={"expected_size": len(payload)})
    upload_id = init.json()["upload_id"]
    await client.patch(
        f"/v1/uploads/{upload_id}",
        content=payload,
        headers={"Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}"},
    )
    return (await client.post(f"/v1/uploads/{upload_id}:finalize", json={})).json()["blob_sha"]


async def test_dataset_delete_round_trip(conf_client) -> None:
    pr = await conf_client.post("/v1/projects", json={"name": "del-rt"})
    pid = pr.json()["project_id"]
    sha = await _upload(conf_client, _jpeg())
    entries = [{"name": "img.jpg", "blob_sha": sha}]
    ds = await conf_client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "ds", "source": {"kind": "upload", "entries": entries}},
    )
    did = ds.json()["dataset_id"]
    await conf_client.post(f"/v1/datasets/{did}/images", json=entries[0])

    delete_resp = await conf_client.delete(f"/v1/projects/{pid}/datasets/{did}")
    assert delete_resp.status_code == 204, delete_resp.text

    get_resp = await conf_client.get(f"/v1/projects/{pid}/datasets/{did}")
    assert get_resp.status_code == 404


async def test_dataset_delete_returns_404_for_unknown_dataset(conf_client) -> None:
    pr = await conf_client.post("/v1/projects", json={"name": "del-404"})
    pid = pr.json()["project_id"]
    resp = await conf_client.delete(f"/v1/projects/{pid}/datasets/01HGHOST00000000000000000A")
    assert resp.status_code == 404


async def test_dataset_delete_rejects_cross_project(conf_client) -> None:
    """A dataset belongs to one project; DELETE under the wrong project
    URL MUST refuse rather than silently succeed."""
    pr1 = await conf_client.post("/v1/projects", json={"name": "del-x1"})
    pr2 = await conf_client.post("/v1/projects", json={"name": "del-x2"})
    pid1 = pr1.json()["project_id"]
    pid2 = pr2.json()["project_id"]
    sha = await _upload(conf_client, _jpeg())
    ds = await conf_client.post(
        f"/v1/projects/{pid1}/datasets",
        json={
            "name": "ds",
            "source": {"kind": "upload", "entries": [{"name": "x.jpg", "blob_sha": sha}]},
        },
    )
    did = ds.json()["dataset_id"]
    resp = await conf_client.delete(f"/v1/projects/{pid2}/datasets/{did}")
    assert resp.status_code in (404, 422)
