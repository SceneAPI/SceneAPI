"""Native scene schemas — sfmapi's wire-stable analogues of pycolmap's
``scene/``, ``geometry/``, ``sfm/``, and ``retrieval/`` types.

These types are **the contract**. Workers read pycolmap's in-memory
objects and write them out as instances of these schemas (typically
into a sealed snapshot's JSON sidecars). Clients consume them without
ever needing pycolmap installed.

Quaternion convention
---------------------
All quaternions on the wire are ``(w, x, y, z)`` (Hamilton, scalar
first) — matching COLMAP's text/binary file convention. Convert
to/from Eigen's ``(x, y, z, w)`` storage at the worker boundary.

Camera models
-------------
``Camera.model`` is the COLMAP camera-model string. Standard pinhole
variants (``SIMPLE_PINHOLE``, ``PINHOLE``, ``SIMPLE_RADIAL``,
``RADIAL``, ``OPENCV``, ``OPENCV_FISHEYE``, ...) carry their usual
intrinsic parameter vector in ``params``. The equirectangular
panorama camera uses the special model :data:`SPHERICAL_CAMERA_MODEL`
(``"SPHERICAL"``) and **MUST** ship an empty ``params`` list — the
spherical projection has no intrinsics, only image dimensions. Use
:func:`is_spherical_camera` rather than string-comparing in callers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Rotation(BaseModel):
    """Hamilton quaternion stored ``(w, x, y, z)``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    w: float
    x: float
    y: float
    z: float


class Rigid3(BaseModel):
    """Rigid SE(3) transform: ``y = R @ x + t``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    rotation: Rotation
    translation: tuple[float, float, float]


class Sim3(BaseModel):
    """Similarity Sim(3) transform: ``y = s * R @ x + t``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    rotation: Rotation
    translation: tuple[float, float, float]
    scale: float


class GpsCoord(BaseModel):
    """WGS84 geographic coordinate (lat/lng in degrees, alt in meters)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    lat: float
    lng: float
    alt: float | None = None
    horiz_accuracy_m: float | None = None
    vert_accuracy_m: float | None = None


class ImuMeasurement(BaseModel):
    """A single IMU sample.

    Used for sequence/SLAM-leaning workflows where camera pose is
    correlated with an IMU stream. ``timestamp_ns`` is the nanosecond
    timestamp on the IMU's clock; ``gyro`` is angular velocity
    (rad/s) and ``accel`` is linear acceleration (m/s²) in the IMU
    body frame.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    timestamp_ns: int
    gyro: tuple[float, float, float]
    accel: tuple[float, float, float]


class PosePrior(BaseModel):
    """Prior on a camera's ``cam_from_world`` pose.

    ``covariance`` is a 36-float row-major 6x6 matrix (rx, ry, rz, tx,
    ty, tz). Diagonal-only priors send only the six diagonal entries
    inside the 36-vector with off-diagonals zero. ``timestamp_ns`` is
    the optional nanosecond timestamp the prior corresponds to —
    needed when the same image appears at multiple times in a
    sequence (rolling shutter, video). ``imu`` is an optional IMU
    sample colocated with the pose prior.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    cam_from_world: Rigid3
    covariance: list[float] | None = Field(default=None, min_length=36, max_length=36)
    gps: GpsCoord | None = None
    timestamp_ns: int | None = None
    imu: ImuMeasurement | None = None


SPHERICAL_CAMERA_MODEL: str = "SPHERICAL"
"""Wire constant for equirectangular-panorama cameras.

Any ``Camera`` with ``model == SPHERICAL_CAMERA_MODEL`` represents a
360x180 equirectangular projection — no focal length / principal
point / distortion. ``params`` **MUST** be empty for these cameras."""


def is_spherical_camera(camera: Camera) -> bool:
    """Return True for equirectangular-panorama cameras."""
    return camera.model == SPHERICAL_CAMERA_MODEL


class Camera(BaseModel):
    """Intrinsics. ``model`` is one of COLMAP's strings (``SIMPLE_RADIAL``,
    ``PINHOLE``, ``OPENCV``, ``OPENCV_FISHEYE``, ...). ``params`` is the
    model-specific parameter vector. The equirectangular panorama
    camera uses ``model == SPHERICAL_CAMERA_MODEL`` with empty
    ``params``."""

    model_config = ConfigDict(populate_by_name=True)

    camera_id: int
    model: str
    width: int
    height: int
    params: list[float]
    has_prior_focal_length: bool = False

    def is_spherical(self) -> bool:
        return is_spherical_camera(self)


class Point2D(BaseModel):
    """A keypoint observation in image space. ``point3d_id`` is null when
    the keypoint is not (yet) part of a 3D track."""

    model_config = ConfigDict(populate_by_name=True)

    xy: tuple[float, float]
    point3d_id: int | None = None


class ImagePose(BaseModel):
    """Per-image pose + (optional) per-image keypoints. The ``points2D``
    list is indexed by ``point2d_idx`` — the same index used inside
    tracks and TwoViewGeometry inlier sets."""

    model_config = ConfigDict(populate_by_name=True)

    image_id: int
    name: str
    camera_id: int
    cam_from_world: Rigid3
    points2D: list[Point2D] = Field(default_factory=list)


class TrackElement(BaseModel):
    """A single observation in a 3D point's track."""

    model_config = ConfigDict(populate_by_name=True)

    image_id: int
    point2d_idx: int


class Track(BaseModel):
    """The full image-space history of a 3D point."""

    model_config = ConfigDict(populate_by_name=True)

    point3d_id: int
    elements: list[TrackElement] = Field(default_factory=list)


class Rig(BaseModel):
    """Multi-sensor rigid rig. ``sensor_from_rig`` maps each sensor
    (camera) into the rig's reference frame; the reference sensor's
    transform is identity."""

    model_config = ConfigDict(populate_by_name=True)

    rig_id: int
    ref_sensor_id: int
    sensor_from_rig: dict[str, Rigid3]


class Frame(BaseModel):
    """A single time-slice of a rig: the rig's pose in the world plus
    the per-sensor data ids (which images belong to this frame).
    ``data_ids`` keys are sensor-id strings; values are image_ids."""

    model_config = ConfigDict(populate_by_name=True)

    frame_id: int
    rig_id: int
    rig_from_world: Rigid3
    data_ids: dict[str, int] = Field(default_factory=dict)


TwoViewGeometryType = Literal[
    "undefined",
    "degenerate",
    "calibrated",
    "uncalibrated",
    "planar",
    "panoramic",
    "planar_or_panoramic",
    "watermark",
    "multiple",
]


class TwoViewGeometry(BaseModel):
    """Verified two-view geometry between an image pair.

    ``F``/``E``/``H`` are 9-float row-major 3x3 matrices, populated
    according to the geometry ``type``: ``F`` for uncalibrated,
    ``E`` for calibrated, ``H`` for planar/panoramic. ``inlier_matches``
    is a flat list of ``(point2d_idx_in_image1, point2d_idx_in_image2)``
    pairs.
    """

    model_config = ConfigDict(populate_by_name=True)

    image_id1: int
    image_id2: int
    type: TwoViewGeometryType
    num_inliers: int
    F: list[float] | None = Field(default=None, min_length=9, max_length=9)
    E: list[float] | None = Field(default=None, min_length=9, max_length=9)
    H: list[float] | None = Field(default=None, min_length=9, max_length=9)
    inlier_matches: list[tuple[int, int]] = Field(default_factory=list)


class CorrespondencePair(BaseModel):
    """Raw (pre-verification) matches between two images.

    ``matches`` is a flat list of ``(point2d_idx_in_image1,
    point2d_idx_in_image2)`` pairs as written by the matcher, before
    geometric verification removes outliers. ``num_matches`` is
    redundant with ``len(matches)`` and provided for fast scans
    where clients want pair counts without parsing the inlier list.
    """

    model_config = ConfigDict(populate_by_name=True)

    image_id1: int
    image_id2: int
    num_matches: int
    matches: list[tuple[int, int]] = Field(default_factory=list)


class CorrespondenceGraphFile(BaseModel):
    """Wire shape of ``correspondence_graph.json``."""

    model_config = ConfigDict(populate_by_name=True)

    pairs: list[CorrespondencePair] = Field(default_factory=list)


class PoseGraphEdge(BaseModel):
    """A relative-pose constraint between two image nodes."""

    model_config = ConfigDict(populate_by_name=True)

    image_id1: int
    image_id2: int
    cam2_from_cam1: Rigid3
    weight: float = 1.0


class PoseGraph(BaseModel):
    """Result of pose-graph optimization (or its input). ``nodes`` carry
    the optimized absolute poses; ``edges`` carry the relative
    constraints that drove them."""

    model_config = ConfigDict(populate_by_name=True)

    nodes: list[ImagePose] = Field(default_factory=list)
    edges: list[PoseGraphEdge] = Field(default_factory=list)


# ---- snapshot file wrappers (top-level shape under sealed snapshot) ---


class CamerasFile(BaseModel):
    """Wire shape of ``cameras.json``."""

    model_config = ConfigDict(populate_by_name=True)

    cameras: list[Camera] = Field(default_factory=list)


class ImagesFile(BaseModel):
    """Wire shape of ``images.json``."""

    model_config = ConfigDict(populate_by_name=True)

    images: list[ImagePose] = Field(default_factory=list)


class RigsFile(BaseModel):
    """Wire shape of ``rigs.json``."""

    model_config = ConfigDict(populate_by_name=True)

    rigs: list[Rig] = Field(default_factory=list)


class FramesFile(BaseModel):
    """Wire shape of ``frames.json``."""

    model_config = ConfigDict(populate_by_name=True)

    frames: list[Frame] = Field(default_factory=list)


class TwoViewGeometriesFile(BaseModel):
    """Wire shape of ``two_view_geometries.json``."""

    model_config = ConfigDict(populate_by_name=True)

    pairs: list[TwoViewGeometry] = Field(default_factory=list)


class PoseGraphFile(BaseModel):
    """Wire shape of ``pose_graph.json``."""

    model_config = ConfigDict(populate_by_name=True)

    pose_graph: PoseGraph


# ---- localization (single-image query against a reconstruction) ---------


class LocalizationResult(BaseModel):
    """Result of localizing a query image against an existing reconstruction.

    ``cam_from_world`` is set when ``success`` is true. ``inlier_matches``
    is a flat list of ``(query_keypoint_idx, point3d_id)`` pairs that
    survived RANSAC. ``diagnostics`` is a free-form blob for solver
    telemetry (number of correspondences, time, etc.) — clients **MUST**
    treat unknown keys as opaque.
    """

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    cam_from_world: Rigid3 | None = None
    num_inliers: int = 0
    inlier_matches: list[tuple[int, int]] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)


__all__ = [
    "SPHERICAL_CAMERA_MODEL",
    "Camera",
    "CamerasFile",
    "CorrespondenceGraphFile",
    "CorrespondencePair",
    "Frame",
    "FramesFile",
    "GpsCoord",
    "ImagePose",
    "ImagesFile",
    "ImuMeasurement",
    "LocalizationResult",
    "Point2D",
    "PoseGraph",
    "PoseGraphEdge",
    "PoseGraphFile",
    "PosePrior",
    "Rig",
    "Rigid3",
    "RigsFile",
    "Rotation",
    "Sim3",
    "Track",
    "TrackElement",
    "TwoViewGeometriesFile",
    "TwoViewGeometry",
    "TwoViewGeometryType",
    "is_spherical_camera",
]
