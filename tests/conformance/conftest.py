"""Shared fixtures for the conformance suite.

The suite supports two backends:

  - **External**: when `SCENEAPI_TEST_BASE_URL` is set (e.g. by the
    standalone runner), tests run against that server over the
    network using a real `httpx.AsyncClient`.

  - **Internal**: when no env var is set, tests fall back to the
    in-process reference app via `httpx.ASGITransport` so the same
    suite can be run as part of CI without standing anything up.

Capability detection lives in `caps`: a fixture that probes the
target once and returns a dict of optional features supported. Tests
that need an optional feature `pytest.skip` if absent.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# These pytest markers tag the requirements being checked. Useful for
# downstream filtering (e.g., `pytest -m must` to only verify MUSTs).
pytestmark = pytest.mark.conformance


def _base_url() -> str | None:
    return os.environ.get("SCENEAPI_TEST_BASE_URL")


def _api_key() -> str | None:
    return os.environ.get("SCENEAPI_TEST_KEY")


@pytest_asyncio.fixture(scope="function")
async def conf_client() -> AsyncIterator[AsyncClient]:
    """Returns an httpx AsyncClient configured for the target server."""
    base = _base_url()
    headers: dict[str, str] = {}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if base:
        async with AsyncClient(base_url=base.rstrip("/"), headers=headers, timeout=30.0) as c:
            yield c
        return
    # In-process fallback. Reuse the same db_setup infra as the rest
    # of the test suite (pulled in via tests/conftest.py autouse).
    from sceneapi.server.main import create_app
    from tests.conftest import db_setup as _db_setup  # noqa: F401  (fixture token)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", headers=headers, timeout=30.0
    ) as c:
        yield c


# Bring the in-process db_setup fixture into scope when no external
# base url is set. When base url IS set, db_setup is a no-op fixture.
@pytest_asyncio.fixture(autouse=True)
async def _db_if_in_process() -> AsyncIterator[None]:
    if _base_url():
        yield
        return
    # Late import + delegate to the existing project-wide db_setup
    # so we get schema creation per-test exactly as the reference
    # tests do.
    from sceneapi.server.db import models  # noqa: F401
    from sceneapi.server.db.base import Base
    from sceneapi.server.db.session import get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def caps(conf_client: AsyncClient) -> dict[str, Any]:
    """Probe the target once for optional capabilities.

    Returns a dict with keys (each True/False or value):
        - spec_version, server_name (from /spec)
        - has_admin_api_keys
        - has_pipelines
        - has_image_thumbnail
        - has_image_batch
        - has_resume
        - has_ws_jobs
        - has_metrics
    """
    out: dict[str, Any] = {}

    spec = await conf_client.get("/spec")
    if spec.status_code == 200:
        body = spec.json()
        out["spec_version"] = body.get("spec_version")
        out["server_name"] = body.get("server", {}).get("name")
    else:
        out["spec_version"] = None
        out["server_name"] = None

    # Best-effort probes via OPTIONS / HEAD where possible. When neither
    # works (some servers reject OPTIONS), fall back to a benign GET that
    # returns a 4xx other than 404 if the route exists.
    async def _exists(path: str, *, method: str = "OPTIONS") -> bool:
        try:
            r = await conf_client.request(method, path)
        except Exception:
            return False
        # Treat 404 strictly; everything else (200/204/405/422) means
        # the route exists.
        return r.status_code != 404

    out["has_admin_api_keys"] = await _exists("/v1/admin/api-keys", method="GET")
    out["has_pipelines"] = await _exists(
        "/v1/projects/__probe__/pipelines/incremental", method="OPTIONS"
    )
    out["has_image_thumbnail"] = True  # detected per-test via 404
    out["has_image_batch"] = True
    out["has_resume"] = True
    out["has_ws_jobs"] = True
    out["has_metrics"] = await _exists("/metrics", method="GET")

    return out


# Helper utilities --------------------------------------------------------


async def upload_blob(client: AsyncClient, payload: bytes) -> str:
    """Walk the chunked upload flow and return the resulting blob sha."""
    init = await client.post("/v1/uploads", json={"expected_size": len(payload)})
    init.raise_for_status()
    upload_id = init.json()["upload_id"]
    last = len(payload) - 1
    patch = await client.patch(
        f"/v1/uploads/{upload_id}",
        content=payload,
        headers={"Content-Range": f"bytes 0-{last}/{len(payload)}"},
    )
    patch.raise_for_status()
    fin = await client.post(f"/v1/uploads/{upload_id}:finalize", json={})
    fin.raise_for_status()
    return fin.json()["blob_sha"]


def real_jpeg(size: int = 64) -> bytes:
    """Render a tiny but valid JPEG so thumbnail / EXIF probes can
    actually decode the bytes."""
    import io

    from PIL import Image as PILImage

    im = PILImage.new("RGB", (size, size), color=(120, 60, 30))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


async def make_project_dataset(client: AsyncClient, *, name: str = "conf") -> tuple[str, str, str]:
    """Create a project + dataset + register one (real) JPEG image.
    Returns `(project_id, dataset_id, blob_sha)`."""
    pr = await client.post("/v1/projects", json={"name": f"{name}-p"})
    pr.raise_for_status()
    pid = pr.json()["project_id"]
    sha = await upload_blob(client, real_jpeg())
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={
            "name": f"{name}-ds",
            "source": {
                "kind": "upload",
                "entries": [{"name": "a.jpg", "blob_sha": sha}],
            },
        },
    )
    ds.raise_for_status()
    did = ds.json()["dataset_id"]
    img = await client.post(f"/v1/datasets/{did}/images", json={"name": "a.jpg", "blob_sha": sha})
    img.raise_for_status()
    return pid, did, sha
