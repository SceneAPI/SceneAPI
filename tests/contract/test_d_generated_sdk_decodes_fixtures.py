"""Replays the same recorded fixtures through the generated Python SDK.

Adding this layer makes the codegen flip safe: as long as both
SDKs decode the same fixture set, swapping consumers from the
hand-rolled to the generated client is a no-op for them.
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
GEN_ROOT = SDK_ROOT / "python" / "sfmapi_client_gen"


def _load_gen_module(rel: str) -> Any:
    """Load a module from the generated SDK by relative path. The
    generated package isn't installable from source, so we wire the
    parent directory onto ``sys.path`` once and use normal imports."""
    parent = str(GEN_ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    name = f"sfmapi_client_gen.{rel}"
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


def test_snapshot_list_empty_decodes_through_generated() -> None:
    _skip_if_not_generated()
    mod = _load_gen_module("models.snapshot_list_response")
    body = load_fixture("snapshot_list_empty")
    s = mod.SnapshotListResponse.from_dict(body)
    assert list(s.seqs) == []
