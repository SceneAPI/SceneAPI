"""OpenAPI document is well-formed and covers the public surface."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_app_openapi_3_1_with_expected_paths() -> None:
    from app.main import create_app

    app = create_app()
    spec = app.openapi()

    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "sfmapi"

    paths = spec.get("paths", {})
    # Critical surface that downstream SDKs and the docs rely on:
    expected = {
        "/healthz",
        "/readyz",
        "/version",
        "/v1/projects",
        "/v1/projects/{project_id}",
        "/v1/projects/{project_id}/datasets",
        "/v1/datasets/{dataset_id}/images",
        "/v1/uploads",
        "/v1/uploads/{upload_id}",
        "/v1/uploads/{upload_id}:finalize",
        "/v1/datasets/{dataset_id}/features",
        "/v1/datasets/{dataset_id}/matches",
        "/v1/datasets/{dataset_id}/verify",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/progress",
        "/v1/jobs/{job_id}:cancel",
        "/v1/jobs/{job_id}:resume",
        "/v1/jobs/{job_id}/events",
        "/v1/projects/{project_id}/pipelines/{recipe}",
        "/v1/reconstructions/{recon_id}",
        "/v1/reconstructions/{recon_id}/submodels",
        "/v1/reconstructions/{recon_id}/snapshots",
        "/v1/reconstructions/{recon_id}/snapshots/{seq}/{name}",
        "/v1/admin/api-keys",
    }
    missing = expected - set(paths)
    assert not missing, f"openapi spec missing paths: {sorted(missing)}"


def test_dump_openapi_script_writes_file(tmp_path) -> None:
    from scripts.dump_openapi import main as dump_main

    out = tmp_path / "openapi.json"
    rc = dump_main(["--out", str(out), "--indent", "2"])
    assert rc == 0
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert body.startswith("{")
    assert '"openapi"' in body
