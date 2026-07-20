from __future__ import annotations

import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from sceneapi.server.schemas.pipeline_spec import (
    FeedForwardSpec,
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


def test_feed_forward_max_init_points_optional_and_ge_1() -> None:
    adapter = TypeAdapter(PipelineSpec)
    default = adapter.validate_python({"kind": "feed_forward"})
    assert isinstance(default, FeedForwardSpec)
    assert default.max_init_points is None

    capped = adapter.validate_python({"kind": "feed_forward", "max_init_points": 50_000})
    assert capped.max_init_points == 50_000

    with pytest.raises(PydanticValidationError):
        adapter.validate_python({"kind": "feed_forward", "max_init_points": 0})
