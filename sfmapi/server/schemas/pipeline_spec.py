"""PipelineSpec — discriminated union of mapping pipelines.

Web layer accepts these. Workers translate them to engine-specific
option classes inside the backend implementation — never out here.

.. rubric:: PipelineSpec kinds

:data:`PipelineSpec` is a tagged union discriminated on the ``kind``
field:

- ``incremental`` -> :class:`IncrementalSpec`
- ``global``      -> :class:`GlobalSpec`
- ``hierarchical``-> :class:`HierarchicalSpec`
- ``spherical``   -> :class:`SphericalSpec`

Forward-compatibility: SDKs MUST treat unknown ``kind`` values as
``unsupported`` rather than failing the whole response. Add new
variants in additive form (new ``kind`` literal + new arm of the
union); never repurpose an existing ``kind``. Mapping stages advertise
portable support via ``map.{kind}`` capability flags (see
``GET /v1/capabilities``). Unsupported stage capabilities fail through
the submitted job's task status.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sfmapi.server.schemas.api.artifacts import ArtifactInputMap

BackendOptions = dict[str, Any]
"""Backend-specific option bag.

Portable fields on the stage specs are the stable sfmapi contract.
``backend_options`` is reserved for options discovered from
``GET /v1/backend/config-schemas`` and interpreted by the selected
backend provider.
"""

PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH = 64
PROVIDER_SELECTOR_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
    r"(?:@[A-Za-z0-9][A-Za-z0-9_.-]{0,63})?$"
)
PROVIDER_SELECTOR_MAX_LENGTH = PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH * 2 + 1


class _SpecBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description=(
            "Optional backend implementation selector when more than one "
            "registered provider can run the same portable mapping recipe."
        ),
    )
    seed: int = 0
    max_runtime_seconds: int | None = None
    snapshot_frames_freq: int | None = 50
    backend_options: BackendOptions = Field(
        default_factory=dict,
        description=(
            "Backend-specific mapping options. Discover supported keys with "
            "GET /v1/backend/config-schemas and keep portable settings in "
            "the top-level spec fields."
        ),
    )
    input_artifacts: ArtifactInputMap = Field(
        default_factory=dict,
        description=(
            "Optional role-keyed input artifact references. Core roles include "
            "verified_matches, snapshot, and submodel; backend-specific roles "
            "may use the same dot-key syntax as artifact kinds."
        ),
    )


class IncrementalSpec(_SpecBase):
    kind: Literal["incremental"] = "incremental"
    init_image_pair: tuple[str, str] | None = None
    multiple_models: bool = True
    max_num_models: int = 50
    min_num_matches: int = 15
    ba_global_use_pba: bool = True
    extract_colors: bool = True


class GlobalSpec(_SpecBase):
    kind: Literal["global"] = "global"
    backend: Literal["AUTO", "BAXX", "CERES"] = "AUTO"
    formulation: Literal["AUTO", "EXPLICIT_SCALE", "ELIMINATED_SCALE"] = "AUTO"
    use_incremental_quality_fallback: bool = True


class HierarchicalSpec(_SpecBase):
    kind: Literal["hierarchical"] = "hierarchical"
    cluster_max_size: int = 100
    cluster_overlap: int = 25


class SphericalSpec(_SpecBase):
    kind: Literal["spherical"] = "spherical"
    panorama: bool = True


PipelineSpec = Annotated[
    IncrementalSpec | GlobalSpec | HierarchicalSpec | SphericalSpec,
    Field(discriminator="kind"),
]


FeatureType = Literal["sift", "superpoint", "aliked", "disk", "r2d2", "d2net", "sosnet"]
"""Canonical names for local feature extractors. The capability flag
for an extractor is ``features.extract.{type}``; the colmap_mod
backend advertises ``features.extract.sift`` and learned-feature plugins
may advertise extractors such as ``features.extract.sosnet``."""


class FeaturesSpec(BaseModel):
    """Type-tagged feature extractor request.

    Backends report which ``type`` values they support via the
    ``features.extract.{type}`` capability flags. Unsupported types
    return 501 with the canonical capability name.

    Backend-specific extractor controls belong in ``backend_options``."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    type: FeatureType = "sift"
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description=(
            "Optional backend implementation selector when more than one "
            "registered provider can run the same feature type, for example "
            "'colmap' or 'hloc'. Portable capability checks still use type."
        ),
    )
    max_num_features: int = 8192
    use_gpu: bool = True
    seed: int = 0
    backend_options: BackendOptions = Field(
        default_factory=dict,
        description=(
            "Backend-specific feature-extraction options. Discover supported "
            "keys with GET /v1/backend/config-schemas."
        ),
    )
    input_artifacts: ArtifactInputMap = Field(
        default_factory=dict,
        description=(
            "Optional role-keyed input artifact references for advanced or "
            "backend-specific feature extraction flows."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _compat_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        feature_type = data.get("type", "sift")

        extractor_options = data.pop("extractor_options", None)
        if extractor_options is not None and "backend_options" not in data:
            data["backend_options"] = extractor_options
        elif isinstance(extractor_options, dict) and isinstance(data.get("backend_options"), dict):
            data["backend_options"] = {**extractor_options, **data["backend_options"]}
        elif extractor_options is not None and not isinstance(extractor_options, dict):
            data["extractor_options"] = extractor_options

        if feature_type == "sift":
            if "sift_max_num_features" in data and "max_num_features" not in data:
                data["max_num_features"] = data["sift_max_num_features"]
            data.pop("sift_max_num_features", None)
            if "sift_first_octave" in data:
                if "backend_options" not in data or isinstance(data.get("backend_options"), dict):
                    backend_options = dict(data.get("backend_options") or {})
                    backend_options.setdefault("sift_first_octave", data["sift_first_octave"])
                    data["backend_options"] = backend_options
                data.pop("sift_first_octave", None)
        return data


PairStrategy = Literal[
    "exhaustive",
    "sequential",
    "spatial",
    "vocabtree",
    "retrieval",
    "from_poses",
    "explicit",
]
"""How to pick which image pairs to match.

  - ``exhaustive``: every pair (O(N²)).
  - ``sequential``: consecutive pairs within ``overlap`` window
    (video / time-ordered).
  - ``spatial``: pairs within a metric distance (needs GPS or pose
    priors).
  - ``vocabtree``: COLMAP vocabulary tree retrieval.
  - ``retrieval``: pairs from a similarity index (VLAD / NetVLAD /
    custom). Requires the dataset to have a built similarity index.
  - ``from_poses``: pairs whose camera centers are within
    ``overlap_distance_m`` AND whose principal axes are within
    ``max_angle_deg``.
  - ``explicit``: use exactly the image-name pairs supplied inline or
    through an uploaded pair-list blob.
"""


class ImagePairRef(BaseModel):
    """One explicit pair of dataset image names."""

    model_config = ConfigDict(extra="forbid")

    image_name1: str = Field(..., min_length=1, max_length=2048)
    image_name2: str = Field(..., min_length=1, max_length=2048)

    @model_validator(mode="after")
    def _reject_self_pair(self) -> Self:
        if self.image_name1 == self.image_name2:
            raise ValueError("explicit image pairs must reference two different images")
        return self


class PairsSpec(BaseModel):
    """Pair-selection strategy. Decoupled from the matcher so the
    standard supports "select pairs with hloc-style retrieval, then
    match with any local-feature matcher" workflows.

    Capability flag is ``pairs.{strategy}``."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    strategy: PairStrategy = "exhaustive"
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description=(
            "Optional backend implementation selector for this pair-selection "
            "stage. Use only to disambiguate providers that expose the same "
            "portable pair capability."
        ),
    )
    overlap: int = 10
    vocab_tree_path: str | None = None
    retrieval_strategy: Literal["dhash", "vlad", "netvlad"] = "vlad"
    retrieval_k: int = 20
    overlap_distance_m: float | None = None
    max_angle_deg: float | None = None
    image_pairs: list[ImagePairRef] | None = Field(
        None,
        description=(
            "Inline image-name pairs for strategy='explicit'. Intended for "
            "small lists; upload large hloc/COLMAP pair files and pass "
            "pairs_blob_sha instead."
        ),
    )
    pairs_blob_sha: str | None = Field(
        None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "Sha256 of a finalized upload containing newline-delimited image "
            "name pairs, one 'image1 image2' pair per line. Only valid with "
            "strategy='explicit'."
        ),
    )
    pairs_blob_format: Literal["image_name_pairs_txt"] = "image_name_pairs_txt"
    backend_options: BackendOptions = Field(
        default_factory=dict,
        description=(
            "Backend-specific pair-selection options. Discover supported keys "
            "with GET /v1/backend/config-schemas."
        ),
    )
    input_artifacts: ArtifactInputMap = Field(
        default_factory=dict,
        description=(
            "Optional role-keyed input artifact references. Use role 'pairs' "
            "for a previously generated pair-selection artifact."
        ),
    )

    @model_validator(mode="after")
    def _validate_explicit_pairs(self) -> Self:
        has_inline = bool(self.image_pairs)
        has_blob = self.pairs_blob_sha is not None
        has_input_artifact = "pairs" in self.input_artifacts
        if self.strategy == "explicit":
            if sum(bool(value) for value in (has_inline, has_blob, has_input_artifact)) != 1:
                raise ValueError(
                    "strategy='explicit' requires exactly one of image_pairs, "
                    "pairs_blob_sha, or input_artifacts.pairs"
                )
        elif has_inline or has_blob or has_input_artifact:
            raise ValueError(
                "image_pairs, pairs_blob_sha, and input_artifacts.pairs are only valid "
                "with strategy='explicit'"
            )
        return self


MatcherType = Literal[
    "nn-mutual",
    "nn-ratio",
    "superglue",
    "lightglue",
    "loftr",
    "mast3r",
]
"""Per-pair matching algorithm. The capability flag is
``matchers.{type}``. Backends pick the subset they implement."""


class MatcherSpec(BaseModel):
    """Per-pair feature matcher.

    ``nn-mutual`` is the COLMAP default (mutual nearest-neighbor).
    ``nn-ratio`` adds Lowe's ratio test. ``superglue`` / ``lightglue``
    are learned matchers. ``loftr`` is semi-dense (no separate
    extractor — set ``FeaturesSpec.type`` to a placeholder)."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    type: MatcherType = "nn-mutual"
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description=(
            "Optional backend implementation selector when more than one "
            "registered provider can run the same matcher type."
        ),
    )
    use_gpu: bool = True
    cross_check: bool = True
    max_ratio: float = 0.8
    max_distance: float = 0.7
    backend_options: BackendOptions = Field(
        default_factory=dict,
        description=(
            "Backend-specific matcher options. Discover supported keys with "
            "GET /v1/backend/config-schemas."
        ),
    )
    input_artifacts: ArtifactInputMap = Field(
        default_factory=dict,
        description=(
            "Optional role-keyed input artifact references. Use role 'features' "
            "to select a feature artifact produced by another backend."
        ),
    )


class VerifySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description=(
            "Optional backend implementation selector for geometric "
            "verification when multiple providers expose matches.verify."
        ),
    )
    use_gpu: bool = True
    min_inlier_ratio: float = 0.25
    backend_options: BackendOptions = Field(
        default_factory=dict,
        description=(
            "Backend-specific geometric-verification options. Discover "
            "supported keys with GET /v1/backend/config-schemas."
        ),
    )
    input_artifacts: ArtifactInputMap = Field(
        default_factory=dict,
        description=(
            "Optional role-keyed input artifact references. Use role 'matches' "
            "to verify a specific match artifact."
        ),
    )


class BundleAdjustmentSpec(BaseModel):
    """Standalone bundle-adjustment spec.

    ``mode`` selects the algorithm:
      - ``standard``: a single ceres / baxx solve over all
        registered cameras + 3D points (capability ``ba.standard``).
      - ``two_stage``: a two-pass refinement (capability ``ba.two_stage``).
      - ``featuremetric``: Pixel-Perfect SfM-style refinement that
        minimizes a CNN-feature error, not raw reprojection
        (capability ``ba.featuremetric``). Requires a backend with
        learned-feature support.
      - ``rig``: rig-aware refinement that shares intrinsics + relative
        extrinsics across a multi-camera rig (capability ``ba.rig``).

    ``loss_kernel`` chooses the robust loss applied to per-residual
    cost: ``squared`` (no robustification), ``huber``, ``cauchy``,
    ``soft_l1``, ``tukey``. ``loss_threshold`` is the kernel scale
    (in pixels for reprojection loss).
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    mode: Literal["standard", "two_stage", "featuremetric", "rig"] = "standard"
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description=(
            "Optional backend implementation selector for bundle adjustment "
            "when multiple providers expose the requested ba.* capability."
        ),
    )
    refine_focal_length: bool = True
    refine_principal_point: bool = False
    refine_extra_params: bool = True
    max_num_iterations: int = 100
    loss_kernel: Literal["squared", "huber", "cauchy", "soft_l1", "tukey"] = "squared"
    loss_threshold: float = 1.0
    backend_options: BackendOptions = Field(
        default_factory=dict,
        description=(
            "Backend-specific bundle-adjustment options. Discover supported "
            "keys with GET /v1/backend/config-schemas."
        ),
    )


# Canonical ``BundleAdjustmentSpec.mode`` -> gating-capability map.
# Single source of truth imported by BOTH the web tier
# (``sfmapi.server.services.sfm_stage_service``) and the worker
# (``sfmapi.server.workers.tasks.ba``). Keep it next to the ``mode`` Literal
# above: a new mode must land here with its capability in the same
# change, so the two consumers can never drift.
BA_MODE_CAPABILITIES: dict[str, str] = {
    "standard": "ba.standard",
    "two_stage": "ba.two_stage",
    "featuremetric": "ba.featuremetric",
    "rig": "ba.rig",
}


__all__ = [
    "BA_MODE_CAPABILITIES",
    "BackendOptions",
    "BundleAdjustmentSpec",
    "FeatureType",
    "FeaturesSpec",
    "GlobalSpec",
    "HierarchicalSpec",
    "ImagePairRef",
    "IncrementalSpec",
    "MatcherSpec",
    "MatcherType",
    "PairStrategy",
    "PairsSpec",
    "PipelineSpec",
    "SphericalSpec",
    "VerifySpec",
]
