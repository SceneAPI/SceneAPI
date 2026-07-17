from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


async def _create_project(client, name: str) -> str:
    r = await client.post("/v1/projects", json={"name": name})
    return r.json()["project_id"]


async def _upload_one(client, payload: bytes) -> str:
    init = await client.post("/v1/uploads", json={"expected_size": len(payload)})
    upload_id = init.json()["upload_id"]
    await client.patch(
        f"/v1/uploads/{upload_id}",
        content=payload,
        headers={"Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}"},
    )
    fin = await client.post(f"/v1/uploads/{upload_id}:finalize")
    return fin.json()["blob_sha"]


async def test_core_request_bodies_reject_unknown_fields(client) -> None:
    bad_project = await client.post("/v1/projects", json={"name": "bad-extra", "typo": True})
    assert bad_project.status_code == 422
    assert bad_project.json()["errors"][0]["type"] == "extra_forbidden"

    bad_api_key = await client.post(
        "/v1/admin/api-keys",
        json={"tenant_id": "default", "typo": True},
    )
    assert bad_api_key.status_code == 422
    assert bad_api_key.json()["errors"][0]["type"] == "extra_forbidden"

    bad_upload = await client.post(
        "/v1/uploads",
        json={"expected_size": 5, "typo": True},
    )
    assert bad_upload.status_code == 422
    assert bad_upload.json()["errors"][0]["type"] == "extra_forbidden"

    upload = await client.post("/v1/uploads", json={"expected_size": 1})
    bad_finalize = await client.post(
        f"/v1/uploads/{upload.json()['upload_id']}:finalize",
        json={"typo": True},
    )
    assert bad_finalize.status_code == 422
    assert bad_finalize.json()["errors"][0]["type"] == "extra_forbidden"

    pid = await _create_project(client, "strict-bodies")
    bad_dataset = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={
            "name": "bad-ds",
            "source": {"kind": "upload", "entries": [], "typo": True},
        },
    )
    assert bad_dataset.status_code == 422
    assert bad_dataset.json()["errors"][0]["type"] == "extra_forbidden"

    sha = await _upload_one(client, b"\xff\xd8\xff\xe0strict")
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "ds", "source": {"kind": "upload", "entries": []}},
    )
    did = ds.json()["dataset_id"]

    bad_image = await client.post(
        f"/v1/datasets/{did}/images",
        json={"name": "bad.jpg", "blob_sha": sha, "typo": True},
    )
    assert bad_image.status_code == 422
    assert bad_image.json()["errors"][0]["type"] == "extra_forbidden"

    bad_batch = await client.post(
        f"/v1/datasets/{did}/images:batchCreate",
        json={"requests": [{"name": "bad.jpg", "blob_sha": sha, "typo": True}]},
    )
    assert bad_batch.status_code == 422
    assert bad_batch.json()["errors"][0]["type"] == "extra_forbidden"

    bad_projection = await client.post(
        f"/v1/datasets/{did}:projectImages",
        json={"operation": "equirectangular_to_cubemap", "typo": True},
    )
    assert bad_projection.status_code == 422
    assert bad_projection.json()["errors"][0]["type"] == "extra_forbidden"

    image = await client.post(
        f"/v1/datasets/{did}/images",
        json={"name": "strict-pose.jpg", "blob_sha": sha},
    )
    image_id = image.json()["image_id"]
    pose_prior = {
        "cam_from_world": {
            "rotation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
            "translation": [0.0, 0.0, 0.0],
        }
    }
    bad_pose = await client.put(
        f"/v1/images/{image_id}/pose_prior",
        json={**pose_prior, "typo": True},
    )
    assert bad_pose.status_code == 422
    assert bad_pose.json()["errors"][0]["type"] == "extra_forbidden"

    bad_bulk_pose = await client.put(
        f"/v1/datasets/{did}/pose_priors",
        json={image_id: {**pose_prior, "typo": True}},
    )
    assert bad_bulk_pose.status_code == 422
    assert bad_bulk_pose.json()["errors"][0]["type"] == "extra_forbidden"


async def test_create_dataset_from_uploads(client) -> None:
    pid = await _create_project(client, "p1")
    sha_a = await _upload_one(client, b"\xff\xd8\xff\xe0aaaaa")
    sha_b = await _upload_one(client, b"\xff\xd8\xff\xe0bbbbb")

    resp = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={
            "name": "ds1",
            "source": {
                "kind": "upload",
                "entries": [
                    {"name": "a.jpg", "blob_sha": sha_a},
                    {"name": "b.jpg", "blob_sha": sha_b},
                ],
            },
            "camera_model": "OPENCV",
            "is_spherical": False,
        },
    )
    assert resp.status_code == 201, resp.text
    d = resp.json()
    assert d["name"] == "ds1"
    assert d["camera_model"] == "OPENCV"
    assert d["manifest_hash"] == ""

    add = await client.post(
        f"/v1/datasets/{d['dataset_id']}/images",
        json={"name": "a.jpg", "blob_sha": sha_a},
    )
    assert add.status_code == 201, add.text

    list_imgs = await client.get(f"/v1/datasets/{d['dataset_id']}/images")
    assert list_imgs.status_code == 200
    assert len(list_imgs.json()["items"]) == 1


async def test_create_image_rejects_non_hex_blob_sha(client) -> None:
    pid = await _create_project(client, "pbadsha")
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "ds", "source": {"kind": "upload", "entries": []}},
    )
    did = ds.json()["dataset_id"]

    resp = await client.post(
        f"/v1/datasets/{did}/images",
        json={"name": "bad.jpg", "blob_sha": "g" * 64},
    )

    assert resp.status_code == 422
    assert resp.json()["errors"][0]["type"] == "string_pattern_mismatch"


async def test_create_image_rejects_blob_sha_and_rel_path(client) -> None:
    pid = await _create_project(client, "pbothsource")
    sha = await _upload_one(client, b"\xff\xd8\xff\xe0both")
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "ds", "source": {"kind": "upload", "entries": []}},
    )
    did = ds.json()["dataset_id"]

    resp = await client.post(
        f"/v1/datasets/{did}/images",
        json={"name": "bad.jpg", "blob_sha": sha, "rel_path": "bad.jpg"},
    )

    assert resp.status_code == 422
    assert "Exactly one of blob_sha or rel_path is required" in resp.text


async def test_batch_create_image_rejects_blob_sha_and_rel_path(client) -> None:
    pid = await _create_project(client, "pbatchbothsource")
    sha = await _upload_one(client, b"\xff\xd8\xff\xe0batchboth")
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "ds", "source": {"kind": "upload", "entries": []}},
    )
    did = ds.json()["dataset_id"]

    resp = await client.post(
        f"/v1/datasets/{did}/images:batchCreate",
        json={"requests": [{"name": "bad.jpg", "blob_sha": sha, "rel_path": "bad.jpg"}]},
    )

    assert resp.status_code == 422
    assert "Exactly one of blob_sha or rel_path is required" in resp.text


async def test_create_dataset_from_local(client, tmp_path: Path) -> None:
    img = tmp_path / "img.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 1024)

    pid = await _create_project(client, "p2")
    resp = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={
            "name": "ds-local",
            "source": {"kind": "local", "root": str(tmp_path)},
            "camera_model": "SIMPLE_RADIAL",
            "is_spherical": True,
        },
    )
    assert resp.status_code == 201, resp.text
    d = resp.json()
    assert d["is_spherical"] is True


async def test_create_local_image_rejects_rel_path_escape(client, tmp_path: Path) -> None:
    root = tmp_path / "images"
    root.mkdir()
    (root / "ok.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 1024)
    (tmp_path / "outside.jpg").write_bytes(b"\xff\xd8\xff\xe0outside")

    pid = await _create_project(client, "plocalescape")
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "ds-local-escape", "source": {"kind": "local", "root": str(root)}},
    )
    assert ds.status_code == 201, ds.text
    did = ds.json()["dataset_id"]

    resp = await client.post(
        f"/v1/datasets/{did}/images",
        json={"name": "logical.jpg", "rel_path": "../outside.jpg"},
    )

    assert resp.status_code == 422
    assert "rel_path must stay" in resp.text


async def test_dataset_unique_per_project(client) -> None:
    pid = await _create_project(client, "p3")
    body = {
        "name": "same",
        "source": {"kind": "upload", "entries": []},
    }
    a = await client.post(f"/v1/projects/{pid}/datasets", json=body)
    b = await client.post(f"/v1/projects/{pid}/datasets", json=body)
    assert a.status_code == 201
    assert b.status_code == 409
