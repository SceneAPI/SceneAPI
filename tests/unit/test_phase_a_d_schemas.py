"""Schema tests for Phase A-D additions.

Verifies the new pluggable specs / mesh schemas / IMU + timestamps
all round-trip cleanly. The pair-selection (``PairsSpec``) and
per-pair matcher (``MatcherSpec``) shapes are the canonical AIP-202
split — the legacy combined ``MatchesSpec`` was retired.
"""

from __future__ import annotations

import pytest

from app.schemas.api.scene import (
    ImuMeasurement,
    MeshFile,
    MeshSummary,
    PosePrior,
    Rigid3,
    Rotation,
)
from app.schemas.pipeline_spec import (
    BundleAdjustmentSpec,
    FeaturesSpec,
    MatcherSpec,
    PairsSpec,
)

pytestmark = pytest.mark.unit


# ---- Phase A: pluggable extractors / matchers / pairs -----------------


def test_features_spec_defaults_to_sift() -> None:
    f = FeaturesSpec()
    assert f.type == "sift"
    assert f.max_num_features == 8192


def test_features_spec_accepts_alternative_type() -> None:
    f = FeaturesSpec(type="superpoint", max_num_features=4096)
    body = f.model_dump()
    assert body["type"] == "superpoint"
    assert body["max_num_features"] == 4096


def test_pairs_spec_strategies() -> None:
    for strategy in ("exhaustive", "sequential", "spatial", "vocabtree", "retrieval", "from_poses"):
        p = PairsSpec(strategy=strategy)
        assert p.strategy == strategy


def test_pairs_spec_retrieval_carries_strategy_and_k() -> None:
    p = PairsSpec(strategy="retrieval", retrieval_strategy="vlad", retrieval_k=30)
    body = p.model_dump()
    assert body["retrieval_strategy"] == "vlad"
    assert body["retrieval_k"] == 30


def test_matcher_spec_defaults_to_nn_mutual() -> None:
    m = MatcherSpec()
    assert m.type == "nn-mutual"


def test_matcher_spec_accepts_learned_types() -> None:
    for t in ("superglue", "lightglue", "loftr", "mast3r"):
        m = MatcherSpec(type=t)
        assert m.type == t


def test_pairs_and_matcher_specs_round_trip_independently() -> None:
    """AIP-202: pair selection and per-pair matching are independent
    shapes; clients pick them separately on every match-stage call."""
    pairs = PairsSpec(strategy="sequential", overlap=15)
    matcher = MatcherSpec(type="nn-ratio", cross_check=False, max_ratio=0.6)
    assert pairs.strategy == "sequential"
    assert pairs.overlap == 15
    assert matcher.type == "nn-ratio"
    assert matcher.cross_check is False
    assert matcher.max_ratio == 0.6


# ---- Phase B: mesh ------------------------------------------------------


def test_mesh_summary_round_trip() -> None:
    s = MeshSummary(
        method="poisson",
        num_vertices=12345,
        num_faces=24680,
        has_vertex_colors=True,
        has_vertex_normals=False,
    )
    parsed = MeshSummary.model_validate_json(s.model_dump_json())
    assert parsed.method == "poisson"
    assert parsed.num_vertices == 12345
    assert parsed.num_faces == 24680
    assert parsed.has_vertex_colors is True


def test_mesh_file_can_carry_url() -> None:
    f = MeshFile(
        summary=MeshSummary(method="delaunay", num_vertices=1, num_faces=1),
        mesh_url="/v1/reconstructions/abc/snapshots/3/mesh.ply",
    )
    body = f.model_dump()
    assert body["mesh_url"].startswith("/v1/")


# ---- Phase C: featuremetric BA ----------------------------------------


def test_ba_spec_accepts_featuremetric_mode() -> None:
    spec = BundleAdjustmentSpec(mode="featuremetric")
    assert spec.mode == "featuremetric"


def test_ba_spec_loss_kernel_default_squared() -> None:
    spec = BundleAdjustmentSpec()
    assert spec.loss_kernel == "squared"
    assert spec.loss_threshold == 1.0


def test_ba_spec_accepts_robust_kernels() -> None:
    for kernel in ("huber", "cauchy", "soft_l1", "tukey"):
        spec = BundleAdjustmentSpec(loss_kernel=kernel, loss_threshold=2.5)
        assert spec.loss_kernel == kernel
        assert spec.loss_threshold == 2.5


# ---- Phase D: PosePrior with IMU + timestamp --------------------------


def _identity_rigid3() -> Rigid3:
    return Rigid3(
        rotation=Rotation(w=1.0, x=0.0, y=0.0, z=0.0),
        translation=(0.0, 0.0, 0.0),
    )


def test_pose_prior_accepts_timestamp_ns() -> None:
    p = PosePrior(cam_from_world=_identity_rigid3(), timestamp_ns=1_700_000_000_000_000_000)
    body = p.model_dump()
    assert body["timestamp_ns"] == 1_700_000_000_000_000_000


def test_pose_prior_accepts_imu() -> None:
    imu = ImuMeasurement(timestamp_ns=42, gyro=(0.01, 0.02, 0.03), accel=(0.1, -9.81, 0.0))
    p = PosePrior(cam_from_world=_identity_rigid3(), imu=imu)
    parsed = PosePrior.model_validate_json(p.model_dump_json())
    assert parsed.imu is not None
    assert parsed.imu.gyro == (0.01, 0.02, 0.03)
    assert parsed.imu.accel == (0.1, -9.81, 0.0)


def test_pose_prior_round_trip_full() -> None:
    p = PosePrior(
        cam_from_world=_identity_rigid3(),
        covariance=[0.0] * 36,
        timestamp_ns=12345,
        imu=ImuMeasurement(timestamp_ns=12345, gyro=(0.0, 0.0, 0.0), accel=(0.0, 0.0, -9.81)),
    )
    parsed = PosePrior.model_validate_json(p.model_dump_json())
    assert parsed.covariance is not None
    assert parsed.timestamp_ns == 12345
    assert parsed.imu is not None
