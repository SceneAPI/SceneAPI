"""Round-trip tests for sceneapi.server.schemas.api.scene types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sceneapi.server.schemas.api.scene import (
    Camera,
    CamerasFile,
    Frame,
    FramesFile,
    GpsCoord,
    ImagePose,
    ImagesFile,
    Point2D,
    PoseGraph,
    PoseGraphEdge,
    PoseGraphFile,
    PosePrior,
    Rig,
    Rigid3,
    RigsFile,
    Rotation,
    Sim3,
    Track,
    TrackElement,
    TwoViewGeometriesFile,
    TwoViewGeometry,
)

pytestmark = pytest.mark.unit


def _identity_rotation() -> Rotation:
    return Rotation(w=1.0, x=0.0, y=0.0, z=0.0)


def _identity_rigid3() -> Rigid3:
    return Rigid3(rotation=_identity_rotation(), translation=(0.0, 0.0, 0.0))


def test_rotation_quaternion_order_is_wxyz() -> None:
    r = Rotation(w=0.7071, x=0.0, y=0.7071, z=0.0)
    body = r.model_dump()
    assert list(body.keys()) == ["w", "x", "y", "z"]
    assert body["w"] == pytest.approx(0.7071)


def test_rigid3_round_trip() -> None:
    r = Rigid3(rotation=_identity_rotation(), translation=(1.0, 2.0, 3.0))
    parsed = Rigid3.model_validate_json(r.model_dump_json())
    assert parsed.translation == (1.0, 2.0, 3.0)
    assert parsed.rotation.w == 1.0


def test_sim3_carries_scale() -> None:
    s = Sim3(rotation=_identity_rotation(), translation=(0.0, 0.0, 0.0), scale=2.5)
    body = s.model_dump()
    assert body["scale"] == 2.5


def test_pose_prior_optional_covariance_and_gps() -> None:
    p = PosePrior(cam_from_world=_identity_rigid3())
    body = p.model_dump()
    assert body["covariance"] is None
    assert body["gps"] is None


def test_pose_prior_with_full_covariance() -> None:
    cov = [0.0] * 36
    p = PosePrior(
        cam_from_world=_identity_rigid3(),
        covariance=cov,
        gps=GpsCoord(lat=37.0, lng=-122.0, alt=10.0),
    )
    parsed = PosePrior.model_validate_json(p.model_dump_json())
    assert parsed.covariance is not None
    assert len(parsed.covariance) == 36
    assert parsed.gps is not None
    assert parsed.gps.lat == 37.0


def test_pose_prior_rejects_wrong_covariance_length() -> None:
    with pytest.raises(ValidationError):
        PosePrior(cam_from_world=_identity_rigid3(), covariance=[0.0] * 9)


def test_camera_round_trip() -> None:
    c = Camera(
        camera_id=1,
        model="SIMPLE_RADIAL",
        width=640,
        height=480,
        params=[500.0, 320.0, 240.0, 0.01],
    )
    parsed = Camera.model_validate_json(c.model_dump_json())
    assert parsed.params == [500.0, 320.0, 240.0, 0.01]


def test_image_pose_with_keypoints() -> None:
    img = ImagePose(
        image_id=42,
        name="img_001.jpg",
        camera_id=1,
        cam_from_world=_identity_rigid3(),
        points2D=[
            Point2D(xy=(100.0, 50.0), point3d_id=7),
            Point2D(xy=(200.0, 75.0)),
        ],
    )
    body = img.model_dump()
    assert body["points2D"][1]["point3d_id"] is None


def test_track_groups_observations_per_point() -> None:
    t = Track(
        point3d_id=99,
        elements=[TrackElement(image_id=1, point2d_idx=0), TrackElement(image_id=3, point2d_idx=4)],
    )
    parsed = Track.model_validate_json(t.model_dump_json())
    assert len(parsed.elements) == 2
    assert parsed.elements[0].image_id == 1


def test_rig_keys_sensors_by_string_id() -> None:
    r = Rig(
        rig_id=1,
        ref_sensor_id=0,
        sensor_from_rig={"0": _identity_rigid3(), "1": _identity_rigid3()},
    )
    body = r.model_dump()
    assert set(body["sensor_from_rig"].keys()) == {"0", "1"}


def test_frame_round_trip() -> None:
    f = Frame(
        frame_id=10, rig_id=1, rig_from_world=_identity_rigid3(), data_ids={"0": 100, "1": 101}
    )
    parsed = Frame.model_validate_json(f.model_dump_json())
    assert parsed.data_ids == {"0": 100, "1": 101}


def test_two_view_geometry_matrices_optional() -> None:
    g = TwoViewGeometry(image_id1=1, image_id2=2, type="calibrated", num_inliers=42)
    assert g.F is None
    assert g.E is None
    assert g.H is None


def test_two_view_geometry_inlier_pairs() -> None:
    g = TwoViewGeometry(
        image_id1=1,
        image_id2=2,
        type="calibrated",
        num_inliers=2,
        E=[1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0],
        inlier_matches=[(0, 1), (3, 4)],
    )
    parsed = TwoViewGeometry.model_validate_json(g.model_dump_json())
    assert parsed.inlier_matches == [(0, 1), (3, 4)]
    assert parsed.E is not None
    assert len(parsed.E) == 9


def test_pose_graph_with_edges() -> None:
    img = ImagePose(image_id=1, name="a.jpg", camera_id=1, cam_from_world=_identity_rigid3())
    edge = PoseGraphEdge(image_id1=1, image_id2=2, cam2_from_cam1=_identity_rigid3(), weight=0.9)
    g = PoseGraph(nodes=[img], edges=[edge])
    parsed = PoseGraph.model_validate_json(g.model_dump_json())
    assert parsed.edges[0].weight == 0.9


def test_file_wrappers_serialize_arrays() -> None:
    cf = CamerasFile(cameras=[Camera(camera_id=1, model="PINHOLE", width=10, height=10, params=[])])
    assert cf.model_dump()["cameras"][0]["camera_id"] == 1

    inf = ImagesFile()
    assert inf.model_dump() == {"images": []}

    rf = RigsFile()
    assert rf.model_dump() == {"rigs": []}

    ff = FramesFile()
    assert ff.model_dump() == {"frames": []}

    tvg = TwoViewGeometriesFile()
    assert tvg.model_dump() == {"pairs": []}

    pgf = PoseGraphFile(pose_graph=PoseGraph())
    body = pgf.model_dump()
    assert body["pose_graph"] == {"nodes": [], "edges": []}
