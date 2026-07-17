"""Tests for pose_graph_emit + two_view_emit."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sfmapi.server.schemas.api.scene import PoseGraphFile, TwoViewGeometriesFile
from sfmapi.server.storage.pose_graph_emit import emit_pose_graph_file
from sfmapi.server.storage.two_view_emit import export_two_view_geometries

pytestmark = pytest.mark.unit


@dataclass
class _StubRotation:
    quat: tuple[float, float, float, float]


@dataclass
class _StubRigid3:
    rotation: _StubRotation
    translation: tuple[float, float, float]


@dataclass
class _StubImage:
    image_id: int
    name: str
    camera_id: int
    cam_from_world: _StubRigid3


@dataclass
class _StubReconstruction:
    images: dict[int, _StubImage] = field(default_factory=dict)


@dataclass
class _StubPGEdge:
    image_id1: int
    image_id2: int
    cam2_from_cam1: _StubRigid3
    weight: float = 1.0


@dataclass
class _StubPoseGraph:
    edges: list[_StubPGEdge] = field(default_factory=list)


def _identity() -> _StubRigid3:
    return _StubRigid3(
        rotation=_StubRotation(quat=(0.0, 0.0, 0.0, 1.0)), translation=(0.0, 0.0, 0.0)
    )


def test_pose_graph_emit_writes_nodes_only_when_no_edges(tmp_path: Path) -> None:
    rec = _StubReconstruction(
        images={
            1: _StubImage(image_id=1, name="a.jpg", camera_id=1, cam_from_world=_identity()),
            2: _StubImage(image_id=2, name="b.jpg", camera_id=1, cam_from_world=_identity()),
        }
    )
    out = emit_pose_graph_file(rec, tmp_path)
    assert out.is_file()
    body = json.loads(out.read_text(encoding="utf-8"))
    parsed = PoseGraphFile.model_validate(body)
    assert len(parsed.pose_graph.nodes) == 2
    assert parsed.pose_graph.edges == []


def test_pose_graph_emit_with_edges(tmp_path: Path) -> None:
    rec = _StubReconstruction(
        images={1: _StubImage(image_id=1, name="a.jpg", camera_id=1, cam_from_world=_identity())}
    )
    pg = _StubPoseGraph(
        edges=[_StubPGEdge(image_id1=1, image_id2=2, cam2_from_cam1=_identity(), weight=0.7)]
    )
    out = emit_pose_graph_file(rec, tmp_path, graph=pg)
    parsed = PoseGraphFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert parsed.pose_graph.edges[0].weight == 0.7


# ---- TwoViewGeometry exporter -------------------------------------------


@dataclass
class _StubTVG:
    config: int  # 2 = calibrated
    F: list[list[float]] | None
    E: list[list[float]] | None
    H: list[list[float]] | None
    inlier_matches: list[tuple[int, int]]


def test_two_view_export_writes_pairs(tmp_path: Path) -> None:
    geom = _StubTVG(
        config=2,
        F=None,
        E=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        H=None,
        inlier_matches=[(0, 1), (3, 4), (5, 6)],
    )
    out = export_two_view_geometries([(1, 2, geom)], tmp_path)
    parsed = TwoViewGeometriesFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert len(parsed.pairs) == 1
    pair = parsed.pairs[0]
    assert (pair.image_id1, pair.image_id2) == (1, 2)
    assert pair.type == "calibrated"
    assert pair.num_inliers == 3
    assert pair.E is not None
    assert len(pair.E) == 9
    assert pair.F is None
    assert pair.H is None
    assert pair.inlier_matches == [(0, 1), (3, 4), (5, 6)]


def test_two_view_export_handles_string_type(tmp_path: Path) -> None:
    @dataclass
    class _StringTVG:
        type: str
        F: None = None
        E: None = None
        H: None = None
        inlier_matches: list = field(default_factory=list)
        num_inliers: int = 5

    out = export_two_view_geometries([(1, 2, _StringTVG(type="PLANAR"))], tmp_path)
    parsed = TwoViewGeometriesFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert parsed.pairs[0].type == "planar"
    assert parsed.pairs[0].num_inliers == 5


def test_two_view_export_empty(tmp_path: Path) -> None:
    out = export_two_view_geometries([], tmp_path)
    parsed = TwoViewGeometriesFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert parsed.pairs == []
