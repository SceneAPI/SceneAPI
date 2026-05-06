"""POST /v1/datasets/{did}:render_cubemap endpoint contract."""

from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage

pytestmark = pytest.mark.e2e


def _jpeg() -> bytes:
    im = PILImage.new("RGB", (32, 32), color=(50, 100, 150))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


async def _upload(client, payload: bytes) -> str:
    init = await client.post("/v1/uploads", json={"expected_size": len(payload)})
    upload_id = init.json()["upload_id"]
    await client.patch(
        f"/v1/uploads/{upload_id}",
        content=payload,
        headers={"Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}"},
    )
    fin = await client.post(f"/v1/uploads/{upload_id}:finalize", json={})
    return fin.json()["blob_sha"]


async def _make_dataset(client, *, is_spherical: bool) -> str:
    pr = await client.post("/v1/projects", json={"name": "rc"})
    pid = pr.json()["project_id"]
    sha = await _upload(client, _jpeg())
    entry = {"name": "pano.jpg", "blob_sha": sha}
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={
            "name": "ds",
            "source": {"kind": "upload", "entries": [entry]},
            "is_spherical": is_spherical,
        },
    )
    did = ds.json()["dataset_id"]
    await client.post(f"/v1/datasets/{did}/images", json=entry)
    return did


async def test_render_cubemap_returns_501_without_capability(client) -> None:
    """Without pycolmap the backend doesn't advertise the
    spherical.render_cubemap capability — request is rejected before
    the dataset/is_spherical check."""
    resp = await client.post("/v1/datasets/01HGHOST00000000000000000A:render_cubemap")
    assert resp.status_code == 501
    assert resp.json()["capability"] == "spherical.render_cubemap"


async def test_render_cubemap_501_even_for_pinhole_dataset(client) -> None:
    """Capability check fires first; the is_spherical=false rejection
    only matters when the backend can do the work."""
    did = await _make_dataset(client, is_spherical=False)
    resp = await client.post(f"/v1/datasets/{did}:render_cubemap")
    assert resp.status_code == 501


async def test_render_cubemap_rejects_face_size_too_large(client) -> None:
    did = await _make_dataset(client, is_spherical=True)
    resp = await client.post(f"/v1/datasets/{did}:render_cubemap", params={"face_size": 99999})
    assert resp.status_code == 422


async def test_render_cubemap_rejects_face_size_too_small(client) -> None:
    did = await _make_dataset(client, is_spherical=True)
    resp = await client.post(f"/v1/datasets/{did}:render_cubemap", params={"face_size": 1})
    assert resp.status_code == 422
