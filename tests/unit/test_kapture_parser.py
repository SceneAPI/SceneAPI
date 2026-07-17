"""Kapture-format parser used by the import worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from sfmapi.server.workers.tasks.kapture_import import (
    _parse_kapture_records,
    _parse_kapture_sensors,
)

pytestmark = pytest.mark.unit


def test_parse_sensors_extracts_camera_intrinsics(tmp_path: Path) -> None:
    body = """\
# kapture format: 1.1
# sensor_id, name, sensor_type, sensor_params
cam0, cam0, camera, PINHOLE, 640, 480, 500.0, 500.0, 320.0, 240.0
cam1, cam1, camera, SIMPLE_RADIAL, 800, 600, 700.0, 400.0, 300.0, 0.01
imu0, imu0, gyro_accelerometer, dummy
"""
    p = tmp_path / "sensors.txt"
    p.write_text(body, encoding="utf-8")
    sensors = _parse_kapture_sensors(p)
    assert len(sensors) == 2  # imu skipped
    assert sensors[0]["model"] == "PINHOLE"
    assert sensors[0]["width"] == 640
    assert sensors[0]["params"] == [500.0, 500.0, 320.0, 240.0]
    assert sensors[1]["model"] == "SIMPLE_RADIAL"


def test_parse_records_extracts_image_paths(tmp_path: Path) -> None:
    body = """\
# kapture format: 1.1
# timestamp, sensor_id, image_path
0, cam0, frame_000000.jpg
1, cam0, frame_000001.jpg
2, cam1, alt/frame_000000.jpg
"""
    p = tmp_path / "records_camera.txt"
    p.write_text(body, encoding="utf-8")
    records = _parse_kapture_records(p)
    assert len(records) == 3
    assert records[0]["image_path"] == "frame_000000.jpg"
    assert records[2]["sensor_id"] == "cam1"


def test_parse_handles_missing_files(tmp_path: Path) -> None:
    assert _parse_kapture_sensors(tmp_path / "nope.txt") == []
    assert _parse_kapture_records(tmp_path / "nope.txt") == []
