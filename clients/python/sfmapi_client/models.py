"""Pydantic models — mirror the server schemas.

We deliberately keep these in the SDK rather than importing from `app.*`
so the SDK can be released independently and consumers don't have to
install the server-side deps (sqlalchemy, alembic, arq, ...).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


# -- Pagination --------------------------------------------------------------


class Page(_Base, Generic[T]):
    items: list[T]
    next_page_token: str | None = None
    total: int | None = None


# -- Projects / datasets / images / uploads ---------------------------------


class ProjectCreate(_Base):
    name: str
    description: str | None = None


class ProjectPatch(_Base):
    """Partial update; unset fields are left untouched."""

    name: str | None = None
    description: str | None = None


class Project(_Base):
    project_id: str
    tenant_id: str
    name: str
    description: str | None = None
    created_at: datetime


class UploadSourceSpec(_Base):
    kind: Literal["upload"] = "upload"
    entries: list[dict[str, str]] = Field(default_factory=list)


class LocalSourceSpec(_Base):
    kind: Literal["local"] = "local"
    root: str
    recursive: bool = True


class S3SourceSpec(_Base):
    kind: Literal["s3"] = "s3"
    bucket: str
    prefix: str


SourceSpec = Annotated[
    UploadSourceSpec | LocalSourceSpec | S3SourceSpec,
    Field(discriminator="kind"),
]


class DatasetCreate(_Base):
    name: str
    source: dict[str, Any]
    camera_model: str = "SIMPLE_RADIAL"
    intrinsics_mode: Literal["single_camera", "per_image", "per_folder"] = "single_camera"
    is_spherical: bool = False
    rig_config: dict[str, Any] | None = None
    respect_exif_orientation: bool = False


class DatasetPatch(_Base):
    name: str | None = None
    camera_model: str | None = None
    intrinsics_mode: Literal["single_camera", "per_image", "per_folder"] | None = None
    is_spherical: bool | None = None
    rig_config: dict[str, Any] | None = None
    respect_exif_orientation: bool | None = None
    active_maskset_id: str | None = None


class ImageCreate(_Base):
    name: str
    blob_sha: str | None = None
    rel_path: str | None = None
    width: int | None = None
    height: int | None = None
    exif: dict[str, Any] | None = None


class BatchCreateImagesRequest(_Base):
    """AIP-231 batch-create request body."""

    requests: list[ImageCreate] = Field(default_factory=list)


class BatchCreateImagesResponse(_Base):
    """AIP-231 batch-create response — created resources in
    request-order."""

    images: list[Image] = Field(default_factory=list)


class Image(_Base):
    image_id: str
    dataset_id: str
    name: str
    content_sha: str
    source_kind: str
    rel_path: str | None = None
    byte_size: int | None = None
    width: int | None = None
    height: int | None = None
    created_at: datetime


class Dataset(_Base):
    dataset_id: str
    tenant_id: str
    project_id: str
    source_id: str
    name: str
    camera_model: str
    intrinsics_mode: str
    is_spherical: bool
    respect_exif_orientation: bool
    rig_config: dict[str, Any] | None = Field(None, alias="rig_config_json")
    active_maskset_id: str | None = None
    manifest_hash: str
    created_at: datetime


class Upload(_Base):
    upload_id: str
    state: str
    expected_size: int
    received_bytes: int
    blob_sha: str | None = None
    expires_at: datetime


class ImageObservation(_Base):
    """Per-image observation row from the visibility sidecar."""

    point3d_id: int
    x: float
    y: float
    kp_idx: int = -1
    error: float | None = None


class PointObservation(_Base):
    """Per-point visibility row — which images observed a 3D point."""

    image_id: int
    x: float
    y: float
    kp_idx: int = -1


class TilesIndex(_Base):
    """Manifest of an octree-tiled point cloud snapshot."""

    bbox_min: list[float]
    bbox_max: list[float]
    levels: list[dict[str, Any]] = Field(default_factory=list)


class ApiKey(_Base):
    api_key_id: str
    tenant_id: str
    label: str | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class ApiKeyCreated(ApiKey):
    """Returned only at creation time. ``raw_key`` is the bearer
    token; the server never reveals it again."""

    raw_key: str


# -- Progress events (mirror of app/schemas/progress_event.py) -------------


ProgressEventKind = Literal[
    "phase_started",
    "phase_progress",
    "phase_completed",
    "metric",
    "snapshot_available",
    "log_line",
    "warning",
    "error",
]


class ProgressEvent(_Base):
    """Loose ProgressEvent shape — every backend kind decoded into a
    single Pydantic model with all kind-specific fields optional. The
    discriminator is ``kind``; readers switch on it and consume the
    relevant fields."""

    schema_version: Literal[1] = 1
    ts: datetime
    job_id: str
    task_id: str | None = None
    seq: int
    kind: ProgressEventKind
    # phase_*
    phase: str | None = None
    current: int | None = None
    total: int | None = None
    rate: float | None = None
    # metric
    key: str | None = None
    value: float | None = None
    # snapshot_available
    snapshot_seq: int | None = None
    summary: dict[str, Any] | None = None
    # log_line / warning / error
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = None
    message: str | None = None
    error_class: str | None = None
    detail: dict[str, Any] | None = None


# -- Jobs / Tasks ------------------------------------------------------------


class Task(_Base):
    task_id: str
    job_id: str
    kind: str
    status: str
    cache_key: str
    inputs_hash: str
    params_hash: str
    outputs_ref: dict[str, Any] | None = None


class Job(_Base):
    job_id: str
    tenant_id: str
    project_id: str
    recipe: str
    status: str
    cancel_requested: bool = False
    cancel_force: bool = False
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_class: str | None = None
    error_message: str | None = None


class JobDetail(Job):
    tasks: list[Task] = Field(default_factory=list)


# -- Reconstructions / submodels --------------------------------------------


class Reconstruction(_Base):
    recon_id: str
    project_id: str
    dataset_id: str
    dataset_snapshot_hash: str
    spec: dict[str, Any]
    rv_id: str
    status: str
    created_at: datetime


class SubModel(_Base):
    submodel_id: str
    recon_id: str
    idx: int
    parent_submodel_id: str | None = None
    summary: dict[str, Any] | None = None
    rigidity: dict[str, Any] | None = None
    snapshot_seq: int | None = None
    sealed_path: str | None = None
    created_at: datetime


# -- Pipeline + stage specs (input shapes for job submission) ---------------


FeatureType = Literal["sift", "superpoint", "aliked", "disk", "r2d2", "d2net"]
PairStrategy = Literal[
    "exhaustive", "sequential", "spatial", "vocabtree", "retrieval", "from_poses"
]
MatcherType = Literal["nn-mutual", "nn-ratio", "superglue", "lightglue", "loftr", "mast3r"]


class FeaturesSpec(_Base):
    version: Literal[1] = 1
    type: FeatureType = "sift"
    max_num_features: int = 8192
    use_gpu: bool = True
    seed: int = 0
    extractor_options: dict[str, Any] = Field(default_factory=dict)
    # Backwards-compat aliases — only meaningful when type == "sift".
    sift_max_num_features: int | None = None
    sift_first_octave: int | None = None


class PairsSpec(_Base):
    """New-style pair-selection spec; pair with :class:`MatcherSpec`."""

    version: Literal[1] = 1
    strategy: PairStrategy = "exhaustive"
    overlap: int = 10
    vocab_tree_path: str | None = None
    retrieval_strategy: Literal["dhash", "vlad", "netvlad"] = "vlad"
    retrieval_k: int = 20
    overlap_distance_m: float | None = None
    max_angle_deg: float | None = None


class MatcherSpec(_Base):
    """Per-pair matcher; pair with :class:`PairsSpec`."""

    version: Literal[1] = 1
    type: MatcherType = "nn-mutual"
    use_gpu: bool = True
    cross_check: bool = True
    max_ratio: float = 0.8
    max_distance: float = 0.7
    matcher_options: dict[str, Any] = Field(default_factory=dict)


class VerifySpec(_Base):
    version: Literal[1] = 1
    use_gpu: bool = True
    min_inlier_ratio: float = 0.25


class BundleAdjustmentSpec(_Base):
    version: Literal[1] = 1
    mode: Literal["standard", "two_stage", "featuremetric"] = "standard"
    refine_focal_length: bool = True
    refine_principal_point: bool = False
    refine_extra_params: bool = True
    max_num_iterations: int = 100
    loss_kernel: Literal["squared", "huber", "cauchy", "soft_l1", "tukey"] = "squared"
    loss_threshold: float = 1.0


class _PipelineSpecBase(_Base):
    version: Literal[1] = 1
    seed: int = 0
    max_runtime_seconds: int | None = None
    snapshot_frames_freq: int | None = 50


class IncrementalSpec(_PipelineSpecBase):
    kind: Literal["incremental"] = "incremental"
    init_image_pair: tuple[str, str] | None = None
    multiple_models: bool = True
    max_num_models: int = 50
    min_num_matches: int = 15
    extract_colors: bool = True


class GlobalSpec(_PipelineSpecBase):
    kind: Literal["global"] = "global"
    backend: Literal["AUTO", "BAXX", "CERES"] = "AUTO"
    formulation: Literal["AUTO", "EXPLICIT_SCALE", "ELIMINATED_SCALE"] = "AUTO"
    use_incremental_quality_fallback: bool = True


class HierarchicalSpec(_PipelineSpecBase):
    kind: Literal["hierarchical"] = "hierarchical"
    cluster_max_size: int = 100
    cluster_overlap: int = 25


class SphericalSpec(_PipelineSpecBase):
    kind: Literal["spherical"] = "spherical"
    panorama: bool = True


PipelineSpec = Annotated[
    IncrementalSpec | GlobalSpec | HierarchicalSpec | SphericalSpec,
    Field(discriminator="kind"),
]


# -- Async-context responses for high-level helpers --------------------------


class JobSubmitResponse(_Base):
    job_id: str
    task_ids: list[str]
    recon_id: str | None = None


class HealthResponse(_Base):
    status: str = "ok"


class BackendVersion(_Base):
    name: str
    version: str
    vendor: str | None = None
    runtime_versions: dict[str, str] = Field(default_factory=dict)


class VersionResponse(_Base):
    sfmapi: str
    backend: BackendVersion | None = None


# -- Capabilities (mirror of app/core/capabilities.py) ---------------------


class BackendInfo(_Base):
    name: str
    version: str
    vendor: str = ""


CAPABILITIES_SCHEMA_VERSION = 1


class Capabilities(_Base):
    """Wire shape of the ``GET /v1/capabilities`` response.

    ``schema_version`` tracks the envelope shape (top-level keys, field
    types) — independent of the feature flags themselves, which are
    negotiated via the ``features`` dict.
    """

    schema_version: int = CAPABILITIES_SCHEMA_VERSION
    backend: BackendInfo
    features: dict[str, bool] = Field(default_factory=dict)

    def supports(self, capability: str) -> bool:
        return bool(self.features.get(capability, False))


# -- Scene wire types (mirror of app/schemas/api/scene.py) -----------------


class Rotation(_Base):
    """Hamilton quaternion stored ``(w, x, y, z)``."""

    w: float
    x: float
    y: float
    z: float


class Rigid3(_Base):
    rotation: Rotation
    translation: tuple[float, float, float]


class Sim3(_Base):
    rotation: Rotation
    translation: tuple[float, float, float]
    scale: float


class GpsCoord(_Base):
    lat: float
    lng: float
    alt: float | None = None
    horiz_accuracy_m: float | None = None
    vert_accuracy_m: float | None = None


class ImuMeasurement(_Base):
    timestamp_ns: int
    gyro: tuple[float, float, float]
    accel: tuple[float, float, float]


class PosePrior(_Base):
    cam_from_world: Rigid3
    covariance: list[float] | None = Field(default=None, min_length=36, max_length=36)
    gps: GpsCoord | None = None
    timestamp_ns: int | None = None
    imu: ImuMeasurement | None = None


SPHERICAL_CAMERA_MODEL = "SPHERICAL"


class Camera(_Base):
    camera_id: int
    model: str
    width: int
    height: int
    params: list[float]
    has_prior_focal_length: bool = False

    def is_spherical(self) -> bool:
        return self.model == SPHERICAL_CAMERA_MODEL


class Point2D(_Base):
    xy: tuple[float, float]
    point3d_id: int | None = None


class ImagePose(_Base):
    image_id: int
    name: str
    camera_id: int
    cam_from_world: Rigid3
    points2D: list[Point2D] = Field(default_factory=list)


class TrackElement(_Base):
    image_id: int
    point2d_idx: int


class Track(_Base):
    point3d_id: int
    elements: list[TrackElement] = Field(default_factory=list)


class Rig(_Base):
    rig_id: int
    ref_sensor_id: int
    sensor_from_rig: dict[str, Rigid3]


class Frame(_Base):
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


class TwoViewGeometry(_Base):
    image_id1: int
    image_id2: int
    type: TwoViewGeometryType
    num_inliers: int
    F: list[float] | None = Field(default=None, min_length=9, max_length=9)
    E: list[float] | None = Field(default=None, min_length=9, max_length=9)
    H: list[float] | None = Field(default=None, min_length=9, max_length=9)
    inlier_matches: list[tuple[int, int]] = Field(default_factory=list)


class CorrespondencePair(_Base):
    image_id1: int
    image_id2: int
    num_matches: int
    matches: list[tuple[int, int]] = Field(default_factory=list)


class PoseGraphEdge(_Base):
    image_id1: int
    image_id2: int
    cam2_from_cam1: Rigid3
    weight: float = 1.0


class PoseGraph(_Base):
    nodes: list[ImagePose] = Field(default_factory=list)
    edges: list[PoseGraphEdge] = Field(default_factory=list)


class CamerasFile(_Base):
    cameras: list[Camera] = Field(default_factory=list)


class ImagesFile(_Base):
    images: list[ImagePose] = Field(default_factory=list)


class RigsFile(_Base):
    rigs: list[Rig] = Field(default_factory=list)


class FramesFile(_Base):
    frames: list[Frame] = Field(default_factory=list)


class TwoViewGeometriesFile(_Base):
    pairs: list[TwoViewGeometry] = Field(default_factory=list)


class CorrespondenceGraphFile(_Base):
    pairs: list[CorrespondencePair] = Field(default_factory=list)


class PoseGraphFile(_Base):
    pose_graph: PoseGraph


class LocalizationResult(_Base):
    success: bool
    cam_from_world: Rigid3 | None = None
    num_inliers: int = 0
    inlier_matches: list[tuple[int, int]] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class DepthMapInfo(_Base):
    image_id: int
    image_name: str
    width: int
    height: int
    depth_min: float
    depth_max: float
    has_normal_map: bool = False


class DenseSummary(_Base):
    num_images: int
    num_depth_maps: int
    num_normal_maps: int
    fused_points: int
    bbox_min: tuple[float, float, float] | None = None
    bbox_max: tuple[float, float, float] | None = None


class DenseManifestFile(_Base):
    summary: DenseSummary
    depth_maps: list[DepthMapInfo] = Field(default_factory=list)


MeshMethod = Literal["poisson", "delaunay"]


class MeshSummary(_Base):
    method: MeshMethod
    num_vertices: int
    num_faces: int
    has_vertex_colors: bool = False
    has_vertex_normals: bool = False
    bbox_min: tuple[float, float, float] | None = None
    bbox_max: tuple[float, float, float] | None = None


class MeshFile(_Base):
    summary: MeshSummary
    mesh_url: str | None = None
