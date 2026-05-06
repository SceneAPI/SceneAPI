"""POST /v1/reconstructions/{recon_id}/localize — endpoint contract.

Doesn't exercise the worker (which needs pycolmap); verifies request
validation + 404 path. The full localize flow is covered by the
real-pycolmap e2e when available.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage

pytestmark = pytest.mark.e2e


def _make_jpeg(size: int = 64) -> bytes:
    im = PILImage.new("RGB", (size, size), color=(50, 100, 150))
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


async def test_localize_rejects_missing_blob_sha(client) -> None:
    resp = await client.post("/v1/reconstructions/01HGHOST00000000000000000A/localize", json={})
    assert resp.status_code == 422


async def test_localize_rejects_short_blob_sha(client) -> None:
    resp = await client.post(
        "/v1/reconstructions/01HGHOST00000000000000000A/localize",
        json={"blob_sha": "tooshort"},
    )
    assert resp.status_code == 422


async def test_localize_returns_501_when_backend_lacks_capability(client) -> None:
    """Without pycolmap the backend doesn't advertise localize.from_memory,
    so POST /localize returns 501 with the canonical capability name."""
    payload = _make_jpeg()
    sha = await _upload(client, payload)
    resp = await client.post(
        "/v1/reconstructions/01HGHOST00000000000000000A/localize",
        json={"blob_sha": sha},
    )
    assert resp.status_code == 501
    assert resp.json()["capability"] == "localize.from_memory"
