"""POST /v1/projects/{pid}/datasets:fromArchive end-to-end.

Exercises the full one-call dataset-from-zip flow against the inline
queue + in-memory blob store: build a zip, ride the chunked-upload
protocol, submit, and assert the dispatcher registered a derived
dataset with the images extracted (and their archive-common prefix
stripped from the registered names).
"""

from __future__ import annotations

import io
import zipfile

import pytest
from PIL import Image as PILImage

pytestmark = pytest.mark.e2e


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (32, 24), color=color).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
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


async def test_from_archive_registers_derived_dataset(client) -> None:
    """A zip laid out like the COLMAP samples
    (``<top>/images/*.jpg`` plus non-image junk) registers exactly the
    images, with the common ``south-building/images/`` prefix stripped
    from the names."""
    pid = (await client.post("/v1/projects", json={"name": "arc"})).json()["project_id"]
    sha = await _upload(
        client,
        _zip(
            {
                "south-building/images/P1.JPG": _jpeg((10, 20, 30)),
                "south-building/images/P2.JPG": _jpeg((40, 50, 60)),
                "south-building/database.db": b"not an image, ignored",
                "south-building/README.txt": b"ignored",
            }
        ),
    )

    submit = await client.post(
        f"/v1/projects/{pid}/datasets:fromArchive",
        json={"blob_sha": sha, "name": "south"},
    )
    assert submit.status_code == 202, submit.text
    job_id = submit.json()["job_id"]

    job = await client.get(f"/v1/jobs/{job_id}")
    assert job.status_code == 200, job.text
    task = job.json()["tasks"][0]
    assert task["kind"] == "import_archive"
    assert task["status"] == "succeeded", task
    outputs = task["outputs_ref"]
    assert outputs["num_images"] == 2
    derived = outputs["derived_dataset"]
    assert derived["dataset_id"]
    # Common prefix stripped — names are the bare filenames.
    assert sorted(img["name"] for img in derived["registered_images"]) == ["P1.JPG", "P2.JPG"]

    images = await client.get(f"/v1/datasets/{derived['dataset_id']}/images")
    assert images.status_code == 200
    names = sorted(item["name"] for item in images.json()["items"])
    assert names == ["P1.JPG", "P2.JPG"]
    # Dimensions were read off the extracted bytes by the dispatcher.
    for item in images.json()["items"]:
        assert item["width"] == 32
        assert item["height"] == 24


async def test_from_archive_honors_image_prefix(client) -> None:
    """image_prefix scopes the import to one subtree; entries outside
    it (even valid images) are not registered."""
    pid = (await client.post("/v1/projects", json={"name": "arc2"})).json()["project_id"]
    sha = await _upload(
        client,
        _zip(
            {
                "capture/images/a.jpg": _jpeg((1, 2, 3)),
                "capture/thumbs/a_thumb.jpg": _jpeg((9, 9, 9)),
            }
        ),
    )

    submit = await client.post(
        f"/v1/projects/{pid}/datasets:fromArchive",
        json={"blob_sha": sha, "image_prefix": "capture/images/"},
    )
    assert submit.status_code == 202, submit.text
    task = (await client.get(f"/v1/jobs/{submit.json()['job_id']}")).json()["tasks"][0]
    assert task["status"] == "succeeded", task
    assert task["outputs_ref"]["num_images"] == 1
    did = task["outputs_ref"]["derived_dataset"]["dataset_id"]
    names = [i["name"] for i in (await client.get(f"/v1/datasets/{did}/images")).json()["items"]]
    assert names == ["a.jpg"]


async def test_from_archive_rejects_zip_with_no_images(client) -> None:
    pid = (await client.post("/v1/projects", json={"name": "arc3"})).json()["project_id"]
    sha = await _upload(client, _zip({"notes.txt": b"no images here"}))

    submit = await client.post(f"/v1/projects/{pid}/datasets:fromArchive", json={"blob_sha": sha})
    assert submit.status_code == 202, submit.text
    detail = (await client.get(f"/v1/jobs/{submit.json()['job_id']}")).json()
    assert detail["tasks"][0]["status"] == "failed"
    # The first failing task's error rolls up onto the job (L13).
    assert "no image files" in (detail["error_message"] or "")


async def test_from_archive_rejects_non_zip_blob(client) -> None:
    pid = (await client.post("/v1/projects", json={"name": "arc4"})).json()["project_id"]
    sha = await _upload(client, b"this is plainly not a zip archive")

    submit = await client.post(f"/v1/projects/{pid}/datasets:fromArchive", json={"blob_sha": sha})
    assert submit.status_code == 202, submit.text
    detail = (await client.get(f"/v1/jobs/{submit.json()['job_id']}")).json()
    assert detail["tasks"][0]["status"] == "failed"
    assert "not a valid zip" in (detail["error_message"] or "")


async def test_from_archive_rejects_bad_blob_sha_shape(client) -> None:
    pid = (await client.post("/v1/projects", json={"name": "arc5"})).json()["project_id"]
    resp = await client.post(
        f"/v1/projects/{pid}/datasets:fromArchive", json={"blob_sha": "tooshort"}
    )
    assert resp.status_code == 422


async def test_from_archive_rejects_unknown_field(client) -> None:
    pid = (await client.post("/v1/projects", json={"name": "arc6"})).json()["project_id"]
    resp = await client.post(
        f"/v1/projects/{pid}/datasets:fromArchive",
        json={"blob_sha": "a" * 64, "bogus": 1},
    )
    assert resp.status_code == 422


async def test_from_archive_unknown_project_is_404_not_orphan_job(client) -> None:
    """A bogus project_id must 404 (parity with dataset-create), not
    create an orphan Job under a non-existent project."""
    sha = await _upload(client, _zip({"images/a.jpg": _jpeg((1, 2, 3))}))
    resp = await client.post(
        "/v1/projects/01HGHOST00000000000000000A/datasets:fromArchive",
        json={"blob_sha": sha},
    )
    assert resp.status_code == 404, resp.text
