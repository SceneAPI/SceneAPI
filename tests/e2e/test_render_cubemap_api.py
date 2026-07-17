"""POST /v1/datasets/{did}:renderCubemap endpoint contract."""

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


async def test_render_cubemap_returns_501_without_capability(client, monkeypatch) -> None:
    """Without a backend or built-in pixel engine, capability rejection wins."""
    from sfmapi.server.core.capabilities import reset_capabilities_cache

    monkeypatch.setattr("sfmapi.server.core.projection_engine.has_projection_engine", lambda: False)
    reset_capabilities_cache()
    resp = await client.post("/v1/datasets/01HGHOST00000000000000000A:renderCubemap")
    assert resp.status_code == 501
    assert resp.json()["capability"] == "projection.equirectangular_to_cubemap"


async def test_render_cubemap_rejects_pinhole_dataset(client) -> None:
    did = await _make_dataset(client, is_spherical=False)
    resp = await client.post(f"/v1/datasets/{did}:renderCubemap")
    assert resp.status_code == 422
    assert "is_spherical=true" in resp.text


async def test_render_cubemap_rejects_face_size_too_large(client) -> None:
    did = await _make_dataset(client, is_spherical=True)
    resp = await client.post(f"/v1/datasets/{did}:renderCubemap", params={"face_size": 99999})
    assert resp.status_code == 422


async def test_render_cubemap_rejects_face_size_too_small(client) -> None:
    did = await _make_dataset(client, is_spherical=True)
    resp = await client.post(f"/v1/datasets/{did}:renderCubemap", params={"face_size": 1})
    assert resp.status_code == 422


async def test_projection_request_validates_equirectangular_dimensions(client) -> None:
    did = await _make_dataset(client, is_spherical=False)
    resp = await client.post(
        f"/v1/datasets/{did}:renderEquirectangular",
        json={"equirectangular": {"width": 1024}},
    )
    assert resp.status_code == 422


async def test_perspective_projection_requires_spherical_dataset(client) -> None:
    from sfmapi.server.adapters.registry import register_backend
    from sfmapi.server.adapters.stub_backend import StubBackend
    from sfmapi.server.core.capabilities import reset_capabilities_cache

    class PerspectiveBackend(StubBackend):
        def capabilities(self) -> set[str]:
            return {"projection.equirectangular_to_perspective"}

    register_backend("stub", PerspectiveBackend)
    reset_capabilities_cache()
    did = await _make_dataset(client, is_spherical=False)

    resp = await client.post(f"/v1/datasets/{did}:renderPerspective", json={})

    assert resp.status_code == 422
    assert "is_spherical=true" in resp.text


async def test_projection_job_writes_manifest_and_artifact(client) -> None:
    from sfmapi.server.adapters.registry import register_backend
    from sfmapi.server.adapters.stub_backend import StubBackend
    from sfmapi.server.core.capabilities import reset_capabilities_cache

    class ProjectionBackend(StubBackend):
        def capabilities(self) -> set[str]:
            return {"projection.equirectangular_to_cubemap"}

        def project_images(self, *, operation, input_image_path, output_path, spec):
            assert operation == "equirectangular_to_cubemap"
            assert spec["face_size"] == 128
            assert input_image_path.is_dir()
            (output_path / "front.jpg").write_bytes(b"fake")
            return {"backend": "projection-test"}

    register_backend("stub", ProjectionBackend)
    reset_capabilities_cache()
    did = await _make_dataset(client, is_spherical=True)

    resp = await client.post(
        f"/v1/datasets/{did}:renderCubemap",
        json={"cubemap": {"face_size": 128, "output": {"dataset_name": "ds"}}},
    )

    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    job = await client.get(f"/v1/jobs/{job_id}")
    assert job.status_code == 200
    task = job.json()["tasks"][0]
    assert task["kind"] == "project_images"
    assert task["status"] == "succeeded"
    outputs = task["outputs_ref"]
    assert outputs["operation"] == "equirectangular_to_cubemap"
    assert outputs["num_files"] == 2
    assert outputs["artifacts"][0]["kind"] == "projection.images.v1"
    derived = outputs["derived_dataset"]
    assert derived["dataset_id"]
    assert derived["name"].startswith("ds-")
    derived_images = await client.get(f"/v1/datasets/{derived['dataset_id']}/images")
    assert derived_images.status_code == 200
    assert [item["name"] for item in derived_images.json()["items"]] == ["front.jpg"]

    artifacts = await client.get(f"/v1/jobs/{job_id}/artifacts")
    assert artifacts.status_code == 200
    assert artifacts.json()["items"][0]["kind"] == "projection.images.v1"
