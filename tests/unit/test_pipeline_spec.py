from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from sfmapi.server.schemas.pipeline_spec import (
    GlobalSpec,
    HierarchicalSpec,
    IncrementalSpec,
    PipelineSpec,
    SphericalSpec,
)

pytestmark = pytest.mark.unit


def test_each_variant_validates() -> None:
    adapter = TypeAdapter(PipelineSpec)
    for cls, kind in (
        (IncrementalSpec, "incremental"),
        (GlobalSpec, "global"),
        (HierarchicalSpec, "hierarchical"),
        (SphericalSpec, "spherical"),
    ):
        obj = adapter.validate_python({"kind": kind})
        assert isinstance(obj, cls)
        assert obj.version == 1


def test_unknown_kind_rejected() -> None:
    adapter = TypeAdapter(PipelineSpec)
    with pytest.raises(ValueError, match="kind"):
        adapter.validate_python({"kind": "made_up_kind"})
