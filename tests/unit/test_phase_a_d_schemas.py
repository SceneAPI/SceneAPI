"""Schema tests for Phase A-D additions.

Verifies the pluggable specs / pose priors / IMU schemas all
round-trip cleanly. The pair-selection (``PairsSpec``) and per-pair
matcher (``MatcherSpec``) shapes are the canonical AIP-202 split —
the legacy combined ``MatchesSpec`` was retired.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.schemas.api.artifacts import ArtifactConversionPlanRequest
from app.schemas.api.projections import ProjectionJobRequest
from app.schemas.api.scene import (
    ImuMeasurement,
    PosePrior,
    Rigid3,
    Rotation,
)
from app.schemas.api.stages import VocabTreeSpec
from app.schemas.pipeline_spec import (
    BundleAdjustmentSpec,
    FeaturesSpec,
    IncrementalSpec,
    MatcherSpec,
    PairsSpec,
    VerifySpec,
)

pytestmark = pytest.mark.unit


# ---- Phase A: pluggable extractors / matchers / pairs -----------------


def test_features_spec_defaults_to_sift() -> None:
    f = FeaturesSpec()
    assert f.type == "sift"
    assert f.max_num_features == 8192


def test_features_spec_accepts_alternative_type() -> None:
    f = FeaturesSpec(
        type="superpoint",
        provider="hloc",
        max_num_features=4096,
        backend_options={"SuperPoint.max_keypoints": 4096},
    )
    body = f.model_dump()
    assert body["type"] == "superpoint"
    assert body["provider"] == "hloc"
    assert body["max_num_features"] == 4096
    assert body["backend_options"] == {"SuperPoint.max_keypoints": 4096}


def test_features_spec_accepts_legacy_sift_aliases() -> None:
    f = FeaturesSpec.model_validate({
        "extractor_options": {"peak_threshold": 0.01},
        "sift_max_num_features": 4096,
        "sift_first_octave": -1,
    })

    assert f.type == "sift"
    assert f.max_num_features == 4096
    assert f.backend_options == {"peak_threshold": 0.01, "sift_first_octave": -1}


def test_features_spec_rejects_sift_aliases_for_non_sift_type() -> None:
    with pytest.raises(PydanticValidationError):
        FeaturesSpec.model_validate({
            "type": "superpoint",
            "sift_max_num_features": 4096,
        })


def test_pairs_spec_strategies() -> None:
    for strategy in ("exhaustive", "sequential", "spatial", "vocabtree", "retrieval", "from_poses"):
        p = PairsSpec(strategy=strategy)
        assert p.strategy == strategy


def test_pairs_spec_retrieval_carries_strategy_and_k() -> None:
    p = PairsSpec(
        strategy="retrieval",
        retrieval_strategy="vlad",
        retrieval_k=30,
        backend_options={"hloc.num_matched": 30},
    )
    body = p.model_dump()
    assert body["retrieval_strategy"] == "vlad"
    assert body["retrieval_k"] == 30
    assert body["backend_options"] == {"hloc.num_matched": 30}


def test_pairs_spec_accepts_explicit_inline_pairs() -> None:
    p = PairsSpec(
        strategy="explicit",
        provider="hloc",
        image_pairs=[{"image_name1": "a.jpg", "image_name2": "b.jpg"}],
    )
    body = p.model_dump()
    assert body["strategy"] == "explicit"
    assert body["provider"] == "hloc"
    assert body["image_pairs"] == [{"image_name1": "a.jpg", "image_name2": "b.jpg"}]


def test_pairs_spec_accepts_explicit_pairs_input_artifact() -> None:
    p = PairsSpec(
        strategy="explicit",
        input_artifacts={
            "pairs": {
                "artifact_id": "01H00000000000000000000000",
                "kind": "pairs.image_names.v1",
            }
        },
    )
    body = p.model_dump()
    assert body["strategy"] == "explicit"
    assert body["input_artifacts"]["pairs"]["kind"] == "pairs.image_names.v1"


def test_pairs_spec_explicit_requires_one_pair_source() -> None:
    with pytest.raises(ValueError, match="requires exactly one"):
        PairsSpec(strategy="explicit")
    with pytest.raises(ValueError, match="requires exactly one"):
        PairsSpec(
            strategy="explicit",
            image_pairs=[{"image_name1": "a.jpg", "image_name2": "b.jpg"}],
            pairs_blob_sha="0" * 64,
        )
    with pytest.raises(ValueError, match="requires exactly one"):
        PairsSpec(
            strategy="explicit",
            image_pairs=[{"image_name1": "a.jpg", "image_name2": "b.jpg"}],
            input_artifacts={
                "pairs": {
                    "artifact_id": "01H00000000000000000000000",
                    "kind": "pairs.image_names.v1",
                }
            },
        )


def test_pairs_spec_rejects_explicit_fields_on_other_strategies() -> None:
    with pytest.raises(ValueError, match="only valid"):
        PairsSpec(
            strategy="sequential",
            image_pairs=[{"image_name1": "a.jpg", "image_name2": "b.jpg"}],
        )


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
    matcher = MatcherSpec(
        type="nn-ratio",
        cross_check=False,
        max_ratio=0.6,
        backend_options={"SiftMatching.max_ratio": 0.6},
    )
    assert pairs.strategy == "sequential"
    assert pairs.overlap == 15
    assert matcher.type == "nn-ratio"
    assert matcher.cross_check is False
    assert matcher.max_ratio == 0.6
    assert matcher.backend_options == {"SiftMatching.max_ratio": 0.6}


def test_verify_and_mapping_specs_accept_backend_options() -> None:
    verify = VerifySpec(provider="colmap", backend_options={"RANSAC.max_error": 4.0})
    mapping = IncrementalSpec(
        provider="colmap",
        backend_options={"Mapper.ba_refine_focal_length": False},
    )
    assert verify.backend_options == {"RANSAC.max_error": 4.0}
    assert mapping.provider == "colmap"
    assert mapping.backend_options == {"Mapper.ba_refine_focal_length": False}


# ---- Phase C: featuremetric BA ----------------------------------------


def test_ba_spec_accepts_featuremetric_mode() -> None:
    spec = BundleAdjustmentSpec(
        mode="featuremetric",
        provider="hloc",
        backend_options={"featuremetric.max_num_iterations": 20},
    )
    assert spec.mode == "featuremetric"
    assert spec.provider == "hloc"
    assert spec.backend_options == {"featuremetric.max_num_iterations": 20}


def test_stage_specs_reject_overlength_provider_selector_components() -> None:
    with pytest.raises(PydanticValidationError):
        VocabTreeSpec(provider="p" * 65)

    with pytest.raises(PydanticValidationError):
        VocabTreeSpec(provider=("p" * 64) + "@" + ("g" * 65))


def test_artifact_and_projection_requests_accept_plugin_qualified_provider() -> None:
    provider = ("p" * 64) + "@" + ("g" * 64)

    assert ArtifactConversionPlanRequest(provider=provider).provider == provider
    assert ProjectionJobRequest(provider=provider).provider == provider


def test_artifact_and_projection_requests_reject_overlength_provider_components() -> None:
    with pytest.raises(PydanticValidationError):
        ArtifactConversionPlanRequest(provider="p" * 65)

    with pytest.raises(PydanticValidationError):
        ProjectionJobRequest(provider=("p" * 64) + "@" + ("g" * 65))


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
