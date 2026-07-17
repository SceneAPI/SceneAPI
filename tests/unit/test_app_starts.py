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
        if mod.startswith(
            ("sceneapi.server.main", "sceneapi.server.api", "pycolmap", "torch", "cv2")
        ):
            sys.modules.pop(mod, None)
    import sceneapi.server.main  # noqa: F401

    forbidden = {"pycolmap", "torch", "cv2", "segment_anything"}
    leaked = forbidden & set(sys.modules)
    assert leaked == set(), f"Web-layer imported heavy deps: {leaked}"


def test_app_does_not_import_numpy() -> None:
    """Lazy-numpy guard: importing ``sceneapi.server.main`` and building the app
    must not pull numpy into the web process. numpy is a query-time /
    worker dependency (``storage.vlad``, ``core.projection_engine``)
    and must be imported lazily inside the functions that use it.

    Runs in a subprocess because numpy is legitimately imported by
    other tests in this process (e.g. the VLAD round-trip tests), so
    an in-process ``sys.modules`` check would be order-dependent.
    """
    code = (
        "import sys\n"
        "import sceneapi.server.main\n"
        "sceneapi.server.main.create_app()\n"
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


def _run_shim_subprocess(code: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"


def test_deprecated_sfmapi_alias_shim_contract() -> None:
    """Pins the one-release ``sfmapi`` -> ``sceneapi`` alias shim
    (SceneAPI migration Phase B): every pre-rename import pattern keeps
    working until 0.2.0, emits exactly one DeprecationWarning naming
    the removal, and resolves to the *same module objects* as the new
    paths (so ``except``/``isinstance``/monkeypatch stay coherent
    across the two spellings) — including deep ``sfmapi.server.*``
    chains, which must not load duplicate modules.

    Runs in a subprocess for a clean import state — the shim warns on
    first import only.
    """
    code = (
        "import warnings\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    import sfmapi\n"
        "dep = [w for w in caught if issubclass(w.category, DeprecationWarning)\n"
        "       and 'sceneapi' in str(w.message)]\n"
        "assert len(dep) == 1, 'import sfmapi must emit one DeprecationWarning'\n"
        "msg = str(dep[0].message)\n"
        "assert 'sceneapi' in msg and '0.2.0' in msg, msg\n"
        "import importlib\n"
        "import sfmapi.runtime\n"
        "import sceneapi.runtime\n"
        "assert sfmapi.runtime is sceneapi.runtime\n"
        "assert sfmapi.runtime.create_app is sceneapi.runtime.create_app\n"
        "from sfmapi.errors import SfmApiError\n"
        "import sceneapi.server.core.errors as real_errors\n"
        "assert SfmApiError is real_errors.SfmApiError\n"
        "import sfmapi.server.core.errors as shim_errors\n"
        "assert shim_errors is real_errors\n"
        "from sfmapi.backends import Plugin\n"
        "import sceneapi.backends\n"
        "assert Plugin is sceneapi.backends.Plugin\n"
        "import sceneapi.server\n"
        "assert sfmapi.__version__ == sceneapi.server.__version__\n"
        "runner = importlib.import_module('sfmapi.server.workers.runner')\n"
        "assert runner is importlib.import_module('sceneapi.server.workers.runner')\n"
        "assert runner.__name__ == 'sceneapi.server.workers.runner'\n"
        "assert real_errors.__spec__.name == 'sceneapi.server.core.errors'\n"
    )
    _run_shim_subprocess(code)


def test_sfmapi_alias_shim_works_in_either_import_order() -> None:
    """The alias must stay identity-preserving regardless of whether
    the canonical ``sceneapi`` modules were imported before or after
    the ``sfmapi`` shim (real deployments hit both orders: plugin
    imports the shim first vs. the server imports sceneapi first)."""
    code = (
        "import sceneapi.server.main\n"
        "import sfmapi.server.main\n"
        "assert sfmapi.server.main is sceneapi.server.main\n"
        "assert sfmapi.server.main.create_app is sceneapi.server.main.create_app\n"
        "import sfmapi.runtime\n"
        "import sceneapi.runtime\n"
        "assert sfmapi.runtime.create_app is sceneapi.runtime.create_app\n"
        "import sfmapi.server.core.errors as shim_errors\n"
        "import sceneapi.server.core.errors as real_errors\n"
        "assert shim_errors is real_errors\n"
    )
    _run_shim_subprocess(code)


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
