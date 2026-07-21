"""Replays every recorded fixture through the generated Python SDK.

This is the only Python-SDK fixture-decode layer: the hand-rolled SDK
(and its ``test_b`` replay file) was removed at 0.1.0 as scheduled, so
the page-envelope and problem-envelope coverage that lived there now
runs here through the generated models. It catches the kind of drift a
static type-shape diff misses: field renames, default-value changes,
optional-vs-required flips, discriminator-value drift on tagged unions.
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

SERVER_ROOT = Path(__file__).resolve().parents[2]
SDK_ROOT = Path(os.environ.get("SFMAPI_SDK_REPO", SERVER_ROOT.parent / "sfmapi-sdk"))
GEN_ROOT = SDK_ROOT / "python" / "scenesdk"


def _load_gen_module(rel: str) -> Any:
    """Load a module from the generated SDK by relative path. The
    generated package isn't installable from source, so we wire the
    parent directory onto ``sys.path`` once and use normal imports."""
    parent = str(GEN_ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    name = f"scenesdk.{rel}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.find_spec(name)
    assert spec is not None, f"missing generated module: {name}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _skip_if_not_generated() -> None:
    if not GEN_ROOT.is_dir():
        pytest.skip(f"generated SDK not present at {GEN_ROOT} (run scripts/regen_sdk.py)")


def test_capabilities_decodes_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.capabilities_out")
    body = load_fixture("capabilities")
    caps = mod.CapabilitiesOut.from_dict(body)
    assert caps.schema_version == 1
    assert caps.backend.name


def test_health_decodes_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.health_response")
    body = load_fixture("healthz")
    h = mod.HealthResponse.from_dict(body)
    assert h.status


def test_version_decodes_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.version_response")
    body = load_fixture("version")
    v = mod.VersionResponse.from_dict(body)
    assert v.sfmapi


def test_spec_decodes_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.spec_response")
    body = load_fixture("spec")
    s = mod.SpecResponse.from_dict(body)
    assert s.spec == "sfmapi"
    assert s.spec_version
    assert s.openapi_url


def test_project_round_trip_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.project_out")
    for name in ("project_get", "project_create"):
        body = load_fixture(name)
        p = mod.ProjectOut.from_dict(body)
        assert p.project_id
        assert p.tenant_id
        assert p.name


def test_dataset_decodes_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.dataset_out")
    body = load_fixture("dataset_create")
    d = mod.DatasetOut.from_dict(body)
    assert d.dataset_id
    assert d.camera_model


def test_upload_decodes_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.upload_out")
    body = load_fixture("upload_init")
    u = mod.UploadOut.from_dict(body)
    assert u.upload_id
    assert u.state == "open"


def test_job_accepted_decodes_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.job_accepted_response")
    body = load_fixture("job_accepted_features")
    js = mod.JobAcceptedResponse.from_dict(body)
    assert js.job_id
    assert isinstance(js.task_ids, list)
    assert len(js.task_ids) >= 1


def test_job_accepted_merge_decodes_through_generated() -> None:
    """The merge envelope must round-trip with its stage-specific typed
    fields — target_recon_id / source_recon_ids — and the provider
    selector intact, not collapsed to an untyped bag."""
    _skip_if_not_generated()
    mod = _load_gen_module("models.job_accepted_response")
    body = load_fixture("job_accepted_merge")
    js = mod.JobAcceptedResponse.from_dict(body)
    assert js.job_id
    assert js.target_recon_id
    assert isinstance(js.source_recon_ids, list)
    assert len(js.source_recon_ids) >= 1
    # provider is a typed field on the generated model even when null.
    assert hasattr(js, "provider")


def test_snapshot_list_empty_decodes_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.snapshot_list_response")
    body = load_fixture("snapshot_list_empty")
    s = mod.SnapshotListResponse.from_dict(body)
    assert list(s.seqs) == []


# ---------------------------------------------------------------------
# Coverage ported from the deleted ``test_b`` (hand-rolled SDK replay):
# page envelopes + RFC 7807 problem envelopes, now decoded through the
# generated models instead of the removed ``sfmapi_client`` ones.
# ---------------------------------------------------------------------


def test_project_list_decodes_as_page_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.page_project_out")
    body = load_fixture("project_list")
    page = mod.PageProjectOut.from_dict(body)
    assert isinstance(page.items, list)


def test_page_empty_decodes_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.page_dataset_out")
    body = load_fixture("page_empty")
    page = mod.PageDatasetOut.from_dict(body)
    assert list(page.items) == []


def test_404_error_envelope_decodes_as_problem_response() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.problem_response")
    body = load_fixture("error_404_project_missing")
    # RFC7807 minimum: title + status are mandatory in the problem
    # document; sfmapi additionally always sets `type` + `detail`.
    problem = mod.ProblemResponse.from_dict(body)
    assert problem.status == 404
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
    _skip_if_not_generated()
    mod = _load_gen_module("models.problem_response")
    body = load_fixture("error_422_validation")
    problem = mod.ProblemResponse.from_dict(body)
    assert problem.status == 422
    # RFC 7807 envelope: type, title, status, detail, instance.
    assert "type" in body
    assert "title" in body
    # ``detail`` is a human-readable summary string.
    assert isinstance(body.get("detail"), str)
    # Per-field Pydantic errors are preserved under ``errors``.
    errors = body.get("errors")
    assert isinstance(errors, list)
    assert errors  # non-empty for the recorded missing-name fixture
    assert all(isinstance(e, dict) and "loc" in e and "msg" in e for e in errors)


def test_404_envelope_is_consistent_across_resource_kinds() -> None:
    """Two distinct 404s (project + dataset) must share the same
    problem-json shape — drift here would break SDK error decoding."""
    _skip_if_not_generated()
    mod = _load_gen_module("models.problem_response")
    for name in ("error_404_project_missing", "error_404_dataset_missing"):
        body = load_fixture(name)
        problem = mod.ProblemResponse.from_dict(body)
        assert problem.status == 404
        assert "title" in body
        assert "detail" in body
