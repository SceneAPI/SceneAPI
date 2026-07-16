"""App boots, health endpoints respond, no heavy imports leak."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_app_does_not_import_pycolmap_or_torch() -> None:
    # Force fresh import.
    for mod in list(sys.modules):
        if mod.startswith(("app.main", "app.api", "pycolmap", "torch", "cv2")):
            sys.modules.pop(mod, None)
    import app.main  # noqa: F401

    forbidden = {"pycolmap", "torch", "cv2", "segment_anything"}
    leaked = forbidden & set(sys.modules)
    assert leaked == set(), f"Web-layer imported heavy deps: {leaked}"


def test_app_does_not_import_numpy() -> None:
    """Lazy-numpy guard: importing ``app.main`` and building the app
    must not pull numpy into the web process. numpy is a query-time /
    worker dependency (``storage.vlad``, ``core.projection_engine``)
    and must be imported lazily inside the functions that use it.

    Runs in a subprocess because numpy is legitimately imported by
    other tests in this process (e.g. the VLAD round-trip tests), so
    an in-process ``sys.modules`` check would be order-dependent.
    """
    code = (
        "import sys\n"
        "import app.main\n"
        "app.main.create_app()\n"
        "assert 'numpy' not in sys.modules, "
        "'web import graph pulled numpy at module scope'\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"


async def test_healthz_ok(client) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_version_shape(client) -> None:
    resp = await client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert "sfmapi" in body
    # `backend` is the BackendVersion envelope; either populated
    # (when a backend is registered) or null. Conftest registers
    # the StubBackend so we expect populated.
    assert "backend" in body
    assert body["backend"] is not None
    assert body["backend"]["name"] == "stub"
