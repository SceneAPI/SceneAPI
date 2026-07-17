"""Tests for sfmapi.server.storage.snapshot_emit using a synthetic Reconstruction stub."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sfmapi.server.schemas.api.scene import CamerasFile, ImagesFile
from sfmapi.server.schemas.points_binary import HEADER_SIZE, RECORD_SIZE, decode_records
from sfmapi.server.storage.snapshot_emit import emit_snapshot_files

pytestmark = pytest.mark.unit


# ---- duck-typed stubs that mimic pycolmap's surface -----------------------


@dataclass
class _StubRotation:
    quat: tuple[float, float, float, float]  # (x, y, z, w) — Eigen order


@dataclass
class _StubRigid3:
    rotation: _StubRotation
    translation: tuple[float, float, float]


@dataclass
class _StubCamera:
    camera_id: int
    model_name: str
    width: int
    height: int
    params: list[float]
    has_prior_focal_length: bool = False


@dataclass
class _StubPoint2D:
    xy: tuple[float, float]
    point3D_id: int = 0xFFFFFFFFFFFFFFFF  # pycolmap's "invalid" sentinel


@dataclass
class _StubImage:
    image_id: int
    name: str
    camera_id: int
    cam_from_world: _StubRigid3
    points2D: list[_StubPoint2D] = field(default_factory=list)


@dataclass
class _StubTrackElement:
    image_id: int
    point2D_idx: int


@dataclass
class _StubTrack:
    elements: list[_StubTrackElement] = field(default_factory=list)


@dataclass
class _StubPoint3D:
    point3D_id: int
    xyz: tuple[float, float, float]
    color: tuple[int, int, int]
    track: _StubTrack


@dataclass
class _StubReconstruction:
    cameras: dict[int, _StubCamera] = field(default_factory=dict)
    images: dict[int, _StubImage] = field(default_factory=dict)
    points3D: dict[int, _StubPoint3D] = field(default_factory=dict)
    rigs: dict[int, object] = field(default_factory=dict)
    frames: dict[int, object] = field(default_factory=dict)


def _identity() -> _StubRigid3:
    return _StubRigid3(
        rotation=_StubRotation(quat=(0.0, 0.0, 0.0, 1.0)), translation=(0.0, 0.0, 0.0)
    )


def _make_recon() -> _StubReconstruction:
    cam = _StubCamera(
        camera_id=1,
        model_name="SIMPLE_RADIAL",
        width=640,
        height=480,
        params=[500.0, 320.0, 240.0, 0.01],
    )
    img1 = _StubImage(
        image_id=1,
        name="a.jpg",
        camera_id=1,
        cam_from_world=_identity(),
        points2D=[
            _StubPoint2D(xy=(100.0, 200.0), point3D_id=10),
            _StubPoint2D(xy=(150.0, 250.0)),  # invalid point3d -> unmatched
        ],
    )
    img2 = _StubImage(
        image_id=2,
        name="b.jpg",
        camera_id=1,
        cam_from_world=_identity(),
        points2D=[_StubPoint2D(xy=(110.0, 210.0), point3D_id=10)],
    )
    p10 = _StubPoint3D(
        point3D_id=10,
        xyz=(1.0, 2.0, 3.0),
        color=(255, 128, 64),
        track=_StubTrack(
            elements=[
                _StubTrackElement(image_id=1, point2D_idx=0),
                _StubTrackElement(image_id=2, point2D_idx=0),
            ]
        ),
    )
    return _StubReconstruction(cameras={1: cam}, images={1: img1, 2: img2}, points3D={10: p10})


def test_emit_writes_all_advertised_files(tmp_path: Path) -> None:
    rec = _make_recon()
    manifest = emit_snapshot_files(rec, tmp_path)
    for name in [
        "cameras.json",
        "images.json",
        "points.bin",
        "points_preview.bin",
        "observations_by_image.json",
        "observations_by_point.json",
        "summary.json",
    ]:
        assert (tmp_path / name).is_file(), f"{name} should be emitted"
    assert manifest["cameras"] == 1
    assert manifest["images"] == 2
    assert manifest["points3D"] == 1


def test_cameras_file_is_round_trippable(tmp_path: Path) -> None:
    emit_snapshot_files(_make_recon(), tmp_path)
    body = json.loads((tmp_path / "cameras.json").read_text(encoding="utf-8"))
    parsed = CamerasFile.model_validate(body)
    assert parsed.cameras[0].model == "SIMPLE_RADIAL"
    assert parsed.cameras[0].params == [500.0, 320.0, 240.0, 0.01]


def test_images_file_carries_pose_and_points2D(tmp_path: Path) -> None:
    emit_snapshot_files(_make_recon(), tmp_path)
    body = json.loads((tmp_path / "images.json").read_text(encoding="utf-8"))
    parsed = ImagesFile.model_validate(body)
    img1 = next(i for i in parsed.images if i.image_id == 1)
    assert len(img1.points2D) == 2
    assert img1.points2D[0].point3d_id == 10
    # invalid sentinel was translated to None
    assert img1.points2D[1].point3d_id is None
    # quaternion was reordered xyzw -> wxyz, identity stays identity
    q = img1.cam_from_world.rotation
    assert (q.w, q.x, q.y, q.z) == (1.0, 0.0, 0.0, 0.0)


def test_points_bin_has_header_and_records(tmp_path: Path) -> None:
    emit_snapshot_files(_make_recon(), tmp_path)
    raw = (tmp_path / "points.bin").read_bytes()
    assert len(raw) == HEADER_SIZE + 1 * RECORD_SIZE
    records, _bmin, _bmax = decode_records(raw)
    assert records[0].point3d_id == 10
    assert records[0].xyz == (1.0, 2.0, 3.0)
    assert records[0].rgb == (255, 128, 64)
    assert records[0].track_len == 2


def test_observations_sidecars_have_inverse_keys(tmp_path: Path) -> None:
    emit_snapshot_files(_make_recon(), tmp_path)
    by_image = json.loads((tmp_path / "observations_by_image.json").read_text(encoding="utf-8"))
    by_point = json.loads((tmp_path / "observations_by_point.json").read_text(encoding="utf-8"))
    # Both images observe point 10, so by_point["10"] has 2 entries.
    assert len(by_point["10"]) == 2
    # Each observation in by_image carries kp_idx + (x, y).
    assert by_image["1"][0]["point3d_id"] == 10
    assert by_image["1"][0]["x"] == 100.0


def test_preview_decimates_when_above_threshold(tmp_path: Path) -> None:
    rec = _StubReconstruction()
    for pid in range(1, 51):
        rec.points3D[pid] = _StubPoint3D(
            point3D_id=pid, xyz=(float(pid), 0.0, 0.0), color=(0, 0, 0), track=_StubTrack()
        )
    emit_snapshot_files(rec, tmp_path, preview_max=10)
    raw_full = (tmp_path / "points.bin").read_bytes()
    raw_prev = (tmp_path / "points_preview.bin").read_bytes()
    full_count = (len(raw_full) - HEADER_SIZE) // RECORD_SIZE
    prev_count = (len(raw_prev) - HEADER_SIZE) // RECORD_SIZE
    assert full_count == 50
    assert prev_count <= 10
