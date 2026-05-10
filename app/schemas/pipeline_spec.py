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
union); never repurpose an existing ``kind``. Backends advertise the
recipes they implement via the ``pipelines.{kind}`` capability flags
(see ``GET /v1/capabilities``); requests against an unsupported
``kind`` return ``501 capability_unavailable``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.schemas.api.artifacts import ArtifactInputMap

BackendOptions = dict[str, Any]
"""Backend-specific option bag.

Portable fields on the stage specs are the stable sfmapi contract.
``backend_options`` is reserved for options discovered from
``GET /v1/backend/config-schemas`` and interpreted by the selected
backend provider.
"""


class _SpecBase(BaseModel):
    version: Literal[1] = 1
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
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


FeatureType = Literal["sift", "superpoint", "aliked", "disk", "r2d2", "d2net"]
"""Canonical names for local feature extractors. The capability flag
for an extractor is ``features.extract.{type}``; the colmap_mod
backend advertises ``features.extract.sift`` and (when pycolmap
exposes it) ``features.extract.aliked``."""


class FeaturesSpec(BaseModel):
    """Type-tagged feature extractor request.

    Backends report which ``type`` values they support via the
    ``features.extract.{type}`` capability flags. Unsupported types
    return 501 with the canonical capability name.

    Backwards compat: the legacy ``sift_max_num_features`` /
    ``sift_first_octave`` fields are accepted as aliases when
    ``type=="sift"``."""

    version: Literal[1] = 1
    type: FeatureType = "sift"
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
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
    extractor_options: BackendOptions = Field(
        default_factory=dict,
        description=(
            "Deprecated compatibility alias for backend-specific extractor "
            "options. Prefer backend_options."
        ),
    )
    # Backwards-compat aliases (only meaningful when type=="sift"):
    sift_max_num_features: int | None = None
    sift_first_octave: int | None = None


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

    version: Literal[1] = 1
    strategy: PairStrategy = "exhaustive"
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
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
        if self.strategy == "explicit":
            if has_inline == has_blob:
                raise ValueError(
                    "strategy='explicit' requires exactly one of image_pairs or pairs_blob_sha"
                )
        elif has_inline or has_blob:
            raise ValueError("image_pairs and pairs_blob_sha are only valid with strategy='explicit'")
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

    version: Literal[1] = 1
    type: MatcherType = "nn-mutual"
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
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
    matcher_options: BackendOptions = Field(
        default_factory=dict,
        description=(
            "Deprecated compatibility alias for backend-specific matcher "
            "options. Prefer backend_options."
        ),
    )


class VerifySpec(BaseModel):
    version: Literal[1] = 1
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
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

    ``loss_kernel`` chooses the robust loss applied to per-residual
    cost: ``squared`` (no robustification), ``huber``, ``cauchy``,
    ``soft_l1``, ``tukey``. ``loss_threshold`` is the kernel scale
    (in pixels for reprojection loss).
    """

    version: Literal[1] = 1
    mode: Literal["standard", "two_stage", "featuremetric"] = "standard"
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
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


__all__ = [
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
