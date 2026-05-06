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

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class _SpecBase(BaseModel):
    version: Literal[1] = 1
    seed: int = 0
    max_runtime_seconds: int | None = None
    snapshot_frames_freq: int | None = 50


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
    max_num_features: int = 8192
    use_gpu: bool = True
    seed: int = 0
    extractor_options: dict = Field(default_factory=dict)
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
"""


class PairsSpec(BaseModel):
    """Pair-selection strategy. Decoupled from the matcher so the
    standard supports "select pairs with hloc-style retrieval, then
    match with any local-feature matcher" workflows.

    Capability flag is ``pairs.{strategy}``."""

    version: Literal[1] = 1
    strategy: PairStrategy = "exhaustive"
    overlap: int = 10
    vocab_tree_path: str | None = None
    retrieval_strategy: Literal["dhash", "vlad", "netvlad"] = "vlad"
    retrieval_k: int = 20
    overlap_distance_m: float | None = None
    max_angle_deg: float | None = None


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
    use_gpu: bool = True
    cross_check: bool = True
    max_ratio: float = 0.8
    max_distance: float = 0.7
    matcher_options: dict = Field(default_factory=dict)


class VerifySpec(BaseModel):
    version: Literal[1] = 1
    use_gpu: bool = True
    min_inlier_ratio: float = 0.25


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
    refine_focal_length: bool = True
    refine_principal_point: bool = False
    refine_extra_params: bool = True
    max_num_iterations: int = 100
    loss_kernel: Literal["squared", "huber", "cauchy", "soft_l1", "tukey"] = "squared"
    loss_threshold: float = 1.0


__all__ = [
    "BundleAdjustmentSpec",
    "FeatureType",
    "FeaturesSpec",
    "GlobalSpec",
    "HierarchicalSpec",
    "IncrementalSpec",
    "MatcherSpec",
    "MatcherType",
    "PairStrategy",
    "PairsSpec",
    "PipelineSpec",
    "SphericalSpec",
    "VerifySpec",
]
