"""Observations + visibility sidecar round-trips."""

from __future__ import annotations

from pathlib import Path

import pytest

from sceneapi.server.storage import observations as obs

pytestmark = pytest.mark.unit


def test_round_trip_image_observations(tmp_path: Path) -> None:
    payload = {
        "img-001": [
            obs.ImageObservationRow(point3d_id=1, x=10.5, y=20.5, kp_idx=0, error=0.4),
            obs.ImageObservationRow(point3d_id=2, x=11.0, y=22.0, kp_idx=1),
        ],
        "img-002": [obs.ImageObservationRow(point3d_id=1, x=15.0, y=18.0, kp_idx=0)],
    }
    obs.write_observations_by_image(tmp_path, by_image=payload)
    assert obs.has_observations(tmp_path)
    a = obs.read_observations_for_image(tmp_path, "img-001")
    assert a is not None
    assert len(a) == 2
    assert a[0]["point3d_id"] == 1
    assert a[0]["error"] == 0.4
    assert "error" not in a[1]
    assert obs.read_observations_for_image(tmp_path, "missing") is None


def test_round_trip_visibility(tmp_path: Path) -> None:
    payload = {
        "1": [
            obs.PointObservationRow(image_id=10, x=5.0, y=5.0, kp_idx=0),
            obs.PointObservationRow(image_id=11, x=4.0, y=6.0, kp_idx=2),
        ],
    }
    obs.write_observations_by_point(tmp_path, by_point=payload)
    assert obs.has_visibility(tmp_path)
    body = obs.read_visibility_for_point(tmp_path, "1")
    assert body is not None
    assert len(body) == 2
    assert body[0]["image_id"] == 10


def test_missing_files_return_none(tmp_path: Path) -> None:
    assert obs.read_observations_for_image(tmp_path, "anything") is None
    assert obs.read_visibility_for_point(tmp_path, "anything") is None
    assert not obs.has_observations(tmp_path)
    assert not obs.has_visibility(tmp_path)
