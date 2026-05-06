"""§6.9.3 Image similarity endpoints."""

from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image as PILImage

pytestmark = pytest.mark.e2e


def _gradient_jpeg(seed: int, size: int = 64) -> bytes:
    im = PILImage.new("RGB", (size, size))
    px = im.load()
    for x in range(size):
        for y in range(size):
            px[x, y] = (
                (x * 7 + seed * 13) % 256,
                (y * 11 + seed * 17) % 256,
                (x + y + seed * 23) % 256,
            )
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


async def _make_dataset_with_images(client, n: int) -> tuple[str, list[str]]:
    pr = await client.post("/v1/projects", json={"name": f"sim-{n}"})
    pid = pr.json()["project_id"]
    payloads = [_gradient_jpeg(i) for i in range(n)]
    shas = [await _upload(client, p) for p in payloads]
    entries = [{"name": f"img_{i:02d}.jpg", "blob_sha": shas[i]} for i in range(n)]
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "ds", "source": {"kind": "upload", "entries": entries}},
    )
    did = ds.json()["dataset_id"]
    image_ids: list[str] = []
    for entry in entries:
        img = await client.post(f"/v1/datasets/{did}/images", json=entry)
        image_ids.append(img.json()["image_id"])
    return did, image_ids


async def test_similarity_returns_neighbors_lazy_build(client) -> None:
    did, image_ids = await _make_dataset_with_images(client, 4)
    resp = await client.get(
        f"/v1/datasets/{did}/similarity",
        params={"image_id": image_ids[0], "k": 3, "strategy": "dhash"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query_image_id"] == image_ids[0]
    assert body["strategy"] == "dhash"
    assert body["k"] == 3
    assert len(body["neighbors"]) == 3
    # Distances are in [0, 64] and sorted ascending.
    distances = [n["distance"] for n in body["neighbors"]]
    assert distances == sorted(distances)
    for n in body["neighbors"]:
        assert 0 <= n["distance"] <= 64
        assert n["image_id"] != image_ids[0]


async def test_identical_images_produce_distance_zero(client) -> None:
    did, image_ids = await _make_dataset_with_images(client, 1)
    # Add a second image whose blob is identical bytes — same dhash.
    payload = _gradient_jpeg(0)
    sha = await _upload(client, payload)
    sha2 = hashlib.sha256(payload).hexdigest()
    assert sha == sha2  # sanity
    img = await client.post(f"/v1/datasets/{did}/images", json={"name": "dup.jpg", "blob_sha": sha})
    new_id = img.json()["image_id"]
    resp = await client.get(
        f"/v1/datasets/{did}/similarity",
        params={"image_id": image_ids[0], "k": 1},
    )
    body = resp.json()
    assert body["neighbors"][0]["image_id"] == new_id
    assert body["neighbors"][0]["distance"] == 0


async def test_unknown_image_returns_404(client) -> None:
    did, _ = await _make_dataset_with_images(client, 2)
    resp = await client.get(f"/v1/datasets/{did}/similarity", params={"image_id": "ghost-id"})
    assert resp.status_code == 404


async def test_unknown_strategy_rejected(client) -> None:
    did, image_ids = await _make_dataset_with_images(client, 2)
    resp = await client.get(
        f"/v1/datasets/{did}/similarity",
        params={"image_id": image_ids[0], "strategy": "nope"},
    )
    assert resp.status_code == 422


async def test_vlad_query_404_when_not_built(client) -> None:
    """Querying VLAD before the index is built returns 404 with a
    pointer to `:build`. (The web tier doesn't require pycolmap to
    query; only the build worker does.)"""
    did, image_ids = await _make_dataset_with_images(client, 2)
    resp = await client.get(
        f"/v1/datasets/{did}/similarity",
        params={"image_id": image_ids[0], "strategy": "vlad"},
    )
    assert resp.status_code == 404
    assert "vlad index not built" in resp.text


async def test_vlad_build_returns_501_without_pycolmap(client) -> None:
    """The build path checks the similarity.vlad capability up front;
    without pycolmap it returns 501 with the canonical name."""
    did, _ = await _make_dataset_with_images(client, 2)
    resp = await client.post(f"/v1/datasets/{did}/similarity:build", params={"strategy": "vlad"})
    assert resp.status_code == 501
    assert resp.json()["capability"] == "similarity.vlad"


async def test_build_endpoint_persists_index(client) -> None:
    did, _ = await _make_dataset_with_images(client, 3)
    resp = await client.post(f"/v1/datasets/{did}/similarity:build", params={"strategy": "dhash"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["strategy"] == "dhash"
    assert body["count"] == 3


async def test_empty_dataset_rejected(client) -> None:
    pr = await client.post("/v1/projects", json={"name": "sim-empty"})
    pid = pr.json()["project_id"]
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "ds", "source": {"kind": "upload", "entries": []}},
    )
    did = ds.json()["dataset_id"]
    resp = await client.get(f"/v1/datasets/{did}/similarity", params={"image_id": "anything"})
    assert resp.status_code == 422
