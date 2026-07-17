"""Pure-Python export-format emitters (NeRFStudio, instant-ngp,
Gaussian Splatting, Kapture)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sceneapi.server.adapters.export_formats import (
    export_gaussian_splatting,
    export_instant_ngp,
    export_kapture,
    export_nerfstudio,
)

pytestmark = pytest.mark.unit


@dataclass
class _Rotation:
    quat: tuple[float, float, float, float]  # xyzw


@dataclass
class _Rigid3:
    rotation: _Rotation
    translation: tuple[float, float, float]


@dataclass
class _Camera:
    camera_id: int
    model_name: str
    width: int
    height: int
    params: list[float]


@dataclass
class _Image:
    image_id: int
    name: str
    camera_id: int
    cam_from_world: _Rigid3
    points2D: list = field(default_factory=list)


@dataclass
class _TrackElement:
    image_id: int
    point2D_idx: int


@dataclass
class _Track:
    elements: list[_TrackElement] = field(default_factory=list)


@dataclass
class _Point3D:
    point3D_id: int
    xyz: tuple[float, float, float]
    color: tuple[int, int, int]
    error: float = 0.0
    track: _Track = field(default_factory=_Track)


@dataclass
class _Reconstruction:
    cameras: dict
    images: dict
    points3D: dict


def _make_recon() -> _Reconstruction:
    cam = _Camera(
        camera_id=1,
        model_name="PINHOLE",
        width=640,
        height=480,
        params=[500.0, 500.0, 320.0, 240.0],
    )
    img = _Image(
        image_id=1,
        name="a.jpg",
        camera_id=1,
        cam_from_world=_Rigid3(_Rotation(quat=(0.0, 0.0, 0.0, 1.0)), translation=(0.0, 0.0, 0.0)),
    )
    p = _Point3D(
        point3D_id=10,
        xyz=(1.0, 2.0, 3.0),
        color=(255, 128, 64),
        track=_Track(elements=[_TrackElement(image_id=1, point2D_idx=0)]),
    )
    return _Reconstruction(cameras={1: cam}, images={1: img}, points3D={10: p})


def test_nerfstudio_emits_transforms_json(tmp_path: Path) -> None:
    out = export_nerfstudio(_make_recon(), tmp_path)
    assert out.name == "transforms.json"
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["camera_model"] == "PINHOLE"
    assert body["w"] == 640
    assert body["h"] == 480
    assert len(body["frames"]) == 1
    frame = body["frames"][0]
    assert frame["file_path"] == "./images/a.jpg"
    matrix = frame["transform_matrix"]
    assert len(matrix) == 4
    assert len(matrix[0]) == 4


def test_instant_ngp_extends_nerfstudio_with_aabb(tmp_path: Path) -> None:
    out = export_instant_ngp(_make_recon(), tmp_path)
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["aabb_scale"] == 16
    assert body["scale"] == 1.0
    assert body["offset"] == [0.5, 0.5, 0.5]


def test_gaussian_splatting_emits_colmap_text_layout(tmp_path: Path) -> None:
    out = export_gaussian_splatting(_make_recon(), tmp_path)
    assert out.name == "0"
    assert out.parent.name == "sparse"
    cameras_txt = (out / "cameras.txt").read_text(encoding="utf-8")
    assert "PINHOLE" in cameras_txt
    images_txt = (out / "images.txt").read_text(encoding="utf-8")
    assert "a.jpg" in images_txt
    points_txt = (out / "points3D.txt").read_text(encoding="utf-8")
    assert "1.0 2.0 3.0" in points_txt or "1 2 3" in points_txt
    assert "255 128 64" in points_txt


def test_kapture_emits_sensors_and_trajectories(tmp_path: Path) -> None:
    out = export_kapture(_make_recon(), tmp_path)
    sensors = (out / "sensors" / "sensors.txt").read_text(encoding="utf-8")
    assert "PINHOLE" in sensors
    records = (out / "sensors" / "records_camera.txt").read_text(encoding="utf-8")
    assert "a.jpg" in records
    traj = (out / "reconstruction" / "trajectories.txt").read_text(encoding="utf-8")
    assert traj.count(",") > 0
