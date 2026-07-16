"""Version coherence across the shipping surfaces (lean audit 5.5).

One release = one version string. The server package
(``app.__version__``), the wheel metadata (``pyproject.toml``), and
the committed OpenAPI document (``docs/_static/openapi.json`` — the
repo-root ``openapi.json`` is gitignored) must agree, or a release
bump ships a spec/SDK that claims a different version than the server
reports. The cross-repo test extends the same guarantee to the Python
SDK packages in the sibling ``sfmapi-sdk`` checkout when present,
mirroring how the other contract tests reach that repo
(``SFMAPI_SDK_REPO`` override, skip when absent).
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest

from app import __version__ as app_version

pytestmark = pytest.mark.contract

SERVER_ROOT = Path(__file__).resolve().parents[2]
SDK_ROOT = Path(os.environ.get("SFMAPI_SDK_REPO", SERVER_ROOT.parent / "sfmapi-sdk"))
COMMITTED_OPENAPI = SERVER_ROOT / "docs" / "_static" / "openapi.json"


def _pyproject_version(path: Path) -> str:
    with path.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_app_version_matches_pyproject() -> None:
    assert app_version == _pyproject_version(SERVER_ROOT / "pyproject.toml"), (
        "app.__version__ and pyproject.toml [project].version disagree — "
        "bump both together when cutting a release."
    )


def test_committed_openapi_info_version_matches_app_version() -> None:
    spec = json.loads(COMMITTED_OPENAPI.read_text(encoding="utf-8"))
    assert spec["info"]["version"] == app_version, (
        f"{COMMITTED_OPENAPI} advertises info.version="
        f"{spec['info']['version']!r} but the server reports "
        f"{app_version!r} — re-dump the spec (scripts/regen_sdk.py) "
        "after bumping the version."
    )


def test_sdk_python_package_versions_match_server() -> None:
    """Cross-repo: both Python SDK packages version-lockstep with the
    server. Skips when the sibling SDK checkout isn't present (same
    guard the other cross-repo contract tests use)."""
    if not SDK_ROOT.is_dir():
        pytest.skip(f"sfmapi-sdk checkout not present at {SDK_ROOT}")
    for rel in ("python/pyproject.toml", "python/sfmapi_client_gen/pyproject.toml"):
        pyproject = SDK_ROOT / rel
        if not pyproject.is_file():
            pytest.skip(f"missing {pyproject}")
        assert _pyproject_version(pyproject) == app_version, (
            f"{pyproject} disagrees with the server version "
            f"{app_version!r} — the SDK packages ship in lockstep with "
            "the wire contract."
        )
