"""Spherical camera schema constants + helper."""

from __future__ import annotations

import pytest

from app.schemas.api.scene import (
    SPHERICAL_CAMERA_MODEL,
    Camera,
    is_spherical_camera,
)

pytestmark = pytest.mark.unit


def test_constant_is_spherical_string() -> None:
    assert SPHERICAL_CAMERA_MODEL == "SPHERICAL"


def test_pinhole_camera_is_not_spherical() -> None:
    cam = Camera(
        camera_id=1,
        model="PINHOLE",
        width=640,
        height=480,
        params=[500.0, 500.0, 320.0, 240.0],
    )
    assert not is_spherical_camera(cam)
    assert not cam.is_spherical()


def test_spherical_camera_round_trips_with_empty_params() -> None:
    cam = Camera(
        camera_id=2,
        model=SPHERICAL_CAMERA_MODEL,
        width=4096,
        height=2048,
        params=[],
    )
    assert cam.is_spherical()
    assert is_spherical_camera(cam)
    parsed = Camera.model_validate_json(cam.model_dump_json())
    assert parsed.is_spherical()
    assert parsed.params == []


def test_spherical_helper_compares_only_to_constant() -> None:
    cam = Camera(camera_id=3, model="spherical", width=10, height=10, params=[])
    # case sensitive — pycolmap's model strings are uppercase
    assert not is_spherical_camera(cam)
