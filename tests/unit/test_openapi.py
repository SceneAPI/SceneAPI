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
        "/v1/artifacts/kinds",
        "/v1/artifacts/formats",
        "/v1/artifacts:import",
        "/v1/artifacts/{artifact_id}",
        "/v1/artifacts/{artifact_id}:conversionPlan",
        "/v1/artifacts/{artifact_id}:convert",
        "/v1/artifacts/{artifact_id}:validate",
        "/v1/artifacts/{artifact_id}/content",
        "/v1/datatypes",
        "/v1/attributes",
        "/v1/operations",
        "/v1/processors",
        "/v1/pipelines",
        "/v1/pipelines:validate",
        "/v1/datasets/{dataset_id}/features",
        "/v1/datasets/{dataset_id}/matches",
        "/v1/datasets/{dataset_id}/verify",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/artifacts",
        "/v1/jobs/{job_id}/progress",
        "/v1/jobs/{job_id}:cancel",
        "/v1/jobs/{job_id}:resume",
        "/v1/jobs/{job_id}/events",
        "/v1/projects/{project_id}/pipelines/{recipe}",
        "/v1/projects/{project_id}/pipelines:run",
        "/v1/reconstructions/{recon_id}",
        "/v1/reconstructions/{recon_id}/artifacts",
        "/v1/reconstructions/{recon_id}/submodels",
        "/v1/reconstructions/{recon_id}/snapshots",
        "/v1/reconstructions/{recon_id}/snapshots/{seq}/{name}",
        "/v1/reconstructions/{recon_id}/snapshots/{seq}/submodels/{idx}/{name}",
        "/v1/admin/api-keys",
    }
    missing = expected - set(paths)
    assert not missing, f"openapi spec missing paths: {sorted(missing)}"


def test_upload_patch_declares_binary_request_body() -> None:
    from app.main import create_app

    request_body = (
        create_app()
        .openapi()["paths"]["/v1/uploads/{upload_id}"]["patch"]
        .get("requestBody")
    )

    assert request_body == {
        "required": True,
        "content": {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }


def test_dump_openapi_script_writes_file(tmp_path) -> None:
    from scripts.dump_openapi import main as dump_main

    out = tmp_path / "openapi.json"
    rc = dump_main(["--out", str(out), "--indent", "2"])
    assert rc == 0
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert body.startswith("{")
    assert '"openapi"' in body
