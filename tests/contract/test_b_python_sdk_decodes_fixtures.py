"""Replay every recorded fixture through the Python SDK's typed
models and assert decoding succeeds.

This catches the kind of drift that a static type-shape diff misses:
field renames, default-value changes, optional-vs-required flips,
discriminator-value drift on tagged unions.

Contract tests run after recording (see ``test_record_fixtures.py``)
so fixtures are always fresh for the running server.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.contract.conftest import load_fixture

pytestmark = pytest.mark.contract


def _load_sdk_models() -> Any:
    """Import the SDK's models module by file path so we don't need
    the SDK repo on PYTHONPATH."""
    server_root = Path(__file__).resolve().parents[2]
    sdk_root = Path(os.environ.get("SFMAPI_SDK_REPO", server_root.parent / "sfmapi-sdk"))
    src = sdk_root / "python" / "sfmapi_client" / "models.py"
    if not src.is_file():
        pytest.skip(f"SDK repo not found at {sdk_root}")
    spec = importlib.util.spec_from_file_location("_sdk_models", src)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_sdk_models"] = mod
    spec.loader.exec_module(mod)
    return mod


SDK = _load_sdk_models()


def test_capabilities_decodes_and_carries_schema_version() -> None:
    body = load_fixture("capabilities")
    caps = SDK.Capabilities.model_validate(body)
    assert caps.schema_version == 1
    assert caps.backend.name
    assert isinstance(caps.features, dict)


def test_health_response_decodes() -> None:
    body = load_fixture("healthz")
    HealthResponse = getattr(SDK, "HealthResponse", None)
    if HealthResponse is None:
        pytest.skip("SDK has no HealthResponse model")
    HealthResponse.model_validate(body)


def test_version_response_decodes() -> None:
    body = load_fixture("version")
    VersionResponse = getattr(SDK, "VersionResponse", None)
    if VersionResponse is None:
        pytest.skip("SDK has no VersionResponse model")
    VersionResponse.model_validate(body)


def test_project_get_decodes() -> None:
    body = load_fixture("project_get")
    SDK.Project.model_validate(body)


def test_project_create_decodes() -> None:
    body = load_fixture("project_create")
    SDK.Project.model_validate(body)


def test_project_list_decodes_as_page() -> None:
    body = load_fixture("project_list")
    # Page is generic over the item type.
    page = SDK.Page[SDK.Project].model_validate(body)
    assert isinstance(page.items, list)


def test_dataset_create_decodes() -> None:
    body = load_fixture("dataset_create")
    SDK.Dataset.model_validate(body)


def test_404_error_envelope_is_problem_json_shaped() -> None:
    body = load_fixture("error_404_project_missing")
    # RFC7807 minimum: title + status are mandatory in the problem
    # document; sfmapi additionally always sets `type` + `detail`.
    assert "status" in body
    assert body["status"] == 404
    assert "title" in body or "detail" in body


def test_422_validation_envelope_is_rfc7807_with_errors_list() -> None:
    """422 validation envelope is RFC 7807 problem+json (consistent
    with every other sfmapi error). The structured per-field Pydantic
    errors are preserved under ``errors`` so machine-readable
    consumers can still surface them; ``detail`` is a human summary.

    Pre-2026-05 fixtures had the FastAPI default shape (top-level
    ``detail`` was a list); the migration is documented in
    ``docs/guides/aip_audit_2026.md``.
    """
    body = load_fixture("error_422_validation")
    # RFC 7807 envelope: type, title, status, detail, instance.
    assert body.get("status") == 422
    assert "type" in body
    assert "title" in body
    # ``detail`` is a human-readable summary string.
    assert isinstance(body.get("detail"), str)
    # Per-field Pydantic errors are preserved under ``errors``.
    errors = body.get("errors")
    assert isinstance(errors, list)
    assert errors  # non-empty for the recorded missing-name fixture
    assert all(isinstance(e, dict) and "loc" in e and "msg" in e for e in errors)


def test_page_empty_has_items_list() -> None:
    body = load_fixture("page_empty")
    page = SDK.Page[SDK.Dataset].model_validate(body)
    assert page.items == []


def test_upload_init_decodes() -> None:
    body = load_fixture("upload_init")
    Upload = getattr(SDK, "Upload", None)
    if Upload is None:
        pytest.skip("SDK has no Upload model")
    u = Upload.model_validate(body)
    assert u.upload_id
    assert u.received_bytes == 0
    assert u.state == "open"


def test_job_accepted_features_decodes() -> None:
    body = load_fixture("job_accepted_features")
    JobAccepted = getattr(SDK, "JobAcceptedResponse", None)
    if JobAccepted is None:
        # Hand-rolled SDK uses JobSubmitResponse for the same wire
        # shape — fall back to that.
        JobAccepted = getattr(SDK, "JobSubmitResponse", None)
    if JobAccepted is None:
        pytest.skip("SDK has no JobAccepted/JobSubmitResponse model")
    js = JobAccepted.model_validate(body)
    assert js.job_id
    assert isinstance(js.task_ids, list)
    assert len(js.task_ids) >= 1


def test_job_accepted_merge_decodes() -> None:
    """The merge envelope is the same JobAcceptedResponse wire shape as
    a single-stage submit. The hand-rolled SDK's minimal JobSubmitResponse
    only models the common fields; the merge-specific typed fields are
    asserted against the generated SDK in test_d."""
    body = load_fixture("job_accepted_merge")
    JobAccepted = getattr(SDK, "JobAcceptedResponse", None) or getattr(
        SDK, "JobSubmitResponse", None
    )
    if JobAccepted is None:
        pytest.skip("SDK has no JobAccepted/JobSubmitResponse model")
    js = JobAccepted.model_validate(body)
    assert js.job_id
    assert isinstance(js.task_ids, list)


def test_snapshot_list_empty_decodes() -> None:
    body = load_fixture("snapshot_list_empty")
    SnapshotList = getattr(SDK, "SnapshotListResponse", None)
    if SnapshotList is None:
        # Hand-rolled SDK predates the typed snapshot envelope; just
        # confirm the dict shape.
        assert "seqs" in body
        assert isinstance(body["seqs"], list)
        return
    s = SnapshotList.model_validate(body)
    assert s.seqs == []


def test_404_envelope_is_consistent_across_resource_kinds() -> None:
    """Two distinct 404s (project + dataset) must share the same
    problem-json shape — drift here would break SDK error decoding."""
    p = load_fixture("error_404_project_missing")
    d = load_fixture("error_404_dataset_missing")
    for body in (p, d):
        assert body["status"] == 404
        assert "title" in body
        assert "detail" in body
