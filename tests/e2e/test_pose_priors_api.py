"""GET / PUT / DELETE /v1/images/{image_id}/pose_prior + dataset bulk."""

from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage

pytestmark = pytest.mark.e2e


def _jpeg(seed: int = 0) -> bytes:
    im = PILImage.new("RGB", (32, 32), color=(seed % 256, 128, 200))
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


async def _make_dataset_with_image(client) -> tuple[str, str]:
    pr = await client.post("/v1/projects", json={"name": "pp"})
    pid = pr.json()["project_id"]
    sha = await _upload(client, _jpeg(0))
    entry = {"name": "img.jpg", "blob_sha": sha}
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "ds", "source": {"kind": "upload", "entries": [entry]}},
    )
    did = ds.json()["dataset_id"]
    img = await client.post(f"/v1/datasets/{did}/images", json=entry)
    return did, img.json()["image_id"]


def _identity_prior() -> dict:
    return {
        "cam_from_world": {
            "rotation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
            "translation": [0.0, 0.0, 0.0],
        }
    }


def _gps_prior() -> dict:
    return {
        "cam_from_world": {
            "rotation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
            "translation": [10.0, 20.0, 5.0],
        },
        "gps": {"lat": 37.0, "lng": -122.0, "alt": 30.0},
    }


async def test_get_returns_null_when_unset(client) -> None:
    _did, image_id = await _make_dataset_with_image(client)
    resp = await client.get(f"/v1/images/{image_id}/pose_prior")
    assert resp.status_code == 200
    assert resp.json() is None


async def test_put_then_get_round_trips(client) -> None:
    _did, image_id = await _make_dataset_with_image(client)
    resp = await client.put(f"/v1/images/{image_id}/pose_prior", json=_gps_prior())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cam_from_world"]["translation"] == [10.0, 20.0, 5.0]
    assert body["gps"]["lat"] == 37.0
    again = await client.get(f"/v1/images/{image_id}/pose_prior")
    assert again.json()["gps"]["lat"] == 37.0


async def test_put_replaces_previous_prior(client) -> None:
    _did, image_id = await _make_dataset_with_image(client)
    await client.put(f"/v1/images/{image_id}/pose_prior", json=_gps_prior())
    await client.put(f"/v1/images/{image_id}/pose_prior", json=_identity_prior())
    resp = await client.get(f"/v1/images/{image_id}/pose_prior")
    assert resp.json()["gps"] is None
    assert resp.json()["cam_from_world"]["translation"] == [0.0, 0.0, 0.0]


async def test_delete_clears_prior(client) -> None:
    _did, image_id = await _make_dataset_with_image(client)
    await client.put(f"/v1/images/{image_id}/pose_prior", json=_gps_prior())
    resp = await client.delete(f"/v1/images/{image_id}/pose_prior")
    assert resp.status_code == 204
    after = await client.get(f"/v1/images/{image_id}/pose_prior")
    assert after.json() is None


async def test_put_rejects_invalid_covariance_length(client) -> None:
    _did, image_id = await _make_dataset_with_image(client)
    bad = _identity_prior()
    bad["covariance"] = [0.0] * 9  # must be 36
    resp = await client.put(f"/v1/images/{image_id}/pose_prior", json=bad)
    assert resp.status_code == 422


async def test_dataset_list_returns_only_images_with_priors(client) -> None:
    did, image_id = await _make_dataset_with_image(client)
    resp = await client.get(f"/v1/datasets/{did}/pose_priors")
    assert resp.json() == {"pose_priors": {}}
    await client.put(f"/v1/images/{image_id}/pose_prior", json=_identity_prior())
    resp = await client.get(f"/v1/datasets/{did}/pose_priors")
    body = resp.json()
    assert image_id in body["pose_priors"]


async def test_dataset_bulk_put_sets_multiple(client) -> None:
    did, image_id = await _make_dataset_with_image(client)
    resp = await client.put(f"/v1/datasets/{did}/pose_priors", json={image_id: _gps_prior()})
    assert resp.status_code == 200
    assert resp.json()["written"] == 1
    after = await client.get(f"/v1/images/{image_id}/pose_prior")
    assert after.json()["gps"]["lat"] == 37.0


async def test_unknown_image_get_returns_404(client) -> None:
    resp = await client.get("/v1/images/01HGHOST00000000000000000A/pose_prior")
    assert resp.status_code == 404


async def test_georegister_returns_501_when_backend_lacks_capability(client) -> None:
    """Without pycolmap the backend doesn't advertise georegister.sim3,
    so POST returns 501 with the canonical capability name."""
    resp = await client.post(
        "/v1/reconstructions/01HGHOST00000000000000000A/georegister",
        json={
            "rotation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
            "translation": [0.0, 0.0, 0.0],
            "scale": 2.0,
        },
    )
    assert resp.status_code == 501
    assert resp.json()["capability"] == "georegister.sim3"


async def test_georegister_rejects_missing_scale(client) -> None:
    resp = await client.post(
        "/v1/reconstructions/01HGHOST00000000000000000A/georegister",
        json={
            "rotation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
            "translation": [0.0, 0.0, 0.0],
        },
    )
    assert resp.status_code == 422
