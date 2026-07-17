from __future__ import annotations

import pytest
from pydantic import ValidationError

from sceneapi.server.core.projections import CUBEMAP_FACE_AXES, CUBEMAP_FACE_ORDER
from sceneapi.server.schemas.api.projections import (
    CubemapProjectionSpec,
    EquirectangularProjectionSpec,
    ProjectionJobRequest,
)

pytestmark = pytest.mark.unit


def test_cubemap_projection_defaults_define_convention() -> None:
    request = ProjectionJobRequest()

    assert request.operation == "equirectangular_to_cubemap"
    assert request.cubemap is not None
    assert request.cubemap.face_order == list(CUBEMAP_FACE_ORDER)
    assert CUBEMAP_FACE_AXES["front"]["forward"] == [0, 0, 1]


def test_cubemap_face_order_must_be_complete() -> None:
    with pytest.raises(ValidationError, match="face_order"):
        CubemapProjectionSpec(face_order=["front", "right", "back", "left", "up", "up"])


def test_equirectangular_dimensions_preserve_two_to_one_ratio() -> None:
    assert EquirectangularProjectionSpec(width=2048, height=1024).width == 2048

    with pytest.raises(ValidationError, match="2 \\* height"):
        EquirectangularProjectionSpec(width=2000, height=1024)
