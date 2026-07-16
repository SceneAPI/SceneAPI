# SPDX-License-Identifier: Apache-2.0
# Copyright the sfmapi authors. Licensed under the Apache License, 2.0
# (see LICENSE).
"""Backend contracts for sfmapi engine integrations.

sfmapi is a wire standard with multiple possible engines. The web tier
and workers never import engine libraries directly. They resolve the
configured backend with :func:`app.adapters.registry.get_backend` and
call optional protocol surfaces exposed by that object.

Backends can implement only the layer they actually support:

- :class:`Backend` is the minimum identity/capability/runtime contract.
- :class:`BackendActionProvider` and :class:`BackendConfigSchemaProvider`
  live in sibling modules and are optional discovery surfaces.
- Backend artifact I/O contracts live in ``app.adapters.backend_artifacts``
  and describe which portable artifact kinds each stage accepts/emits.
- Stage protocols such as :class:`FeatureBackend` and
  :class:`MappingBackend` are implemented only by backends that support
  portable sfmapi stages.
- :class:`SfmBackend` remains as the full legacy/progressive protocol
  for complete in-process or artifact-compatible SfM engines.

This split lets CLI/vendor/research backends expose native actions
without dozens of placeholder stage methods. When a portable worker
needs a stage method, it must call :func:`require_backend_method`; a
backend that lacks the method then returns the normal
``CapabilityUnavailableError``/501 shape instead of an ``AttributeError``.

Long-running methods may accept an additional keyword-only
``progress: app.adapters.progress.ProgressReporter | None`` argument.
Workers pass it only when the backend method advertises support for
that keyword, so adding progress reporting is backwards compatible.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from app.adapters.progress import ProgressReporter
from app.core.errors import CapabilityUnavailableError


@runtime_checkable
class BackendIdentity(Protocol):
    """Backend identity fields surfaced by `/v1/version` and discovery."""

    @property
    def name(self) -> str:
        """Canonical short name, e.g. ``"pycolmap"`` or ``"realityscan_cli"``."""

    @property
    def version(self) -> str:
        """Backend adapter version string."""

    @property
    def vendor(self) -> str:
        """Optional human-readable vendor or upstream project."""


@runtime_checkable
class Backend(BackendIdentity, Protocol):
    """Minimum contract every registered backend must satisfy."""

    def capabilities(self) -> set[str]:
        """Portable sfmapi capability names this backend implements.

        This set must contain only names from
        :data:`app.core.capabilities.ALL_KNOWN`. Backend-native tools
        belong in the action catalog, not here.
        """

    def runtime_versions(self) -> dict[str, str]:
        """Engine/dependency versions that influence cache identity."""


@runtime_checkable
class FeatureBackend(Backend, Protocol):
    """Portable feature, pair-selection, matching, and verification stages."""

    def extract_features(
        self,
        *,
        database_path: Path,
        image_root: Path,
        image_list: list[str],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """Run feature extraction over ``image_list`` into the SfM database."""

    def match(self, *, database_path: Path, mode: str, options: dict[str, Any]) -> dict[str, Any]:
        """Run feature matching for the given pair-selection strategy."""

    def verify_matches(self, *, database_path: Path, options: dict[str, Any]) -> dict[str, Any]:
        """Run geometric verification on existing matches."""


@runtime_checkable
class ObservationBackend(Backend, Protocol):
    """Read observation sidecars from a backend feature/match store."""

    def read_keypoints(
        self,
        *,
        database_path: Path,
        image_id: int,
    ) -> tuple[list[list[float]], bytes, int]:
        """Read keypoints and descriptors for one image."""

    def iter_two_view_geometries(self, *, database_path: Path) -> Iterator[tuple[int, int, Any]]:
        """Yield verified two-view geometry rows."""

    def iter_correspondences(self, *, database_path: Path) -> Iterator[tuple[int, int, Any]]:
        """Yield raw match correspondences."""


@runtime_checkable
class MappingBackend(Backend, Protocol):
    """Portable mapping stages."""

    def run_mapping(
        self,
        *,
        kind: str,
        db_path: Path,
        image_root: Path,
        sparse_root: Path,
        job_dir: Path,
        spec: dict[str, Any],
        pose_priors: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[Any]]:
        """Run incremental/global/hierarchical/spherical mapping."""


@runtime_checkable
class RefinementBackend(Backend, Protocol):
    """Portable refinement and model-edit stages."""

    def bundle_adjustment(
        self, *, model_path: Path, output_path: Path, spec: dict[str, Any]
    ) -> dict[str, Any]:
        """Run bundle adjustment."""

    def triangulate(
        self,
        *,
        model_path: Path,
        database_path: Path,
        image_root: Path,
        output_path: Path,
    ) -> dict[str, Any]:
        """Re-triangulate against an existing database."""

    def relocalize(
        self,
        *,
        model_path: Path,
        database_path: Path,
        image_root: Path,
        output_path: Path,
        image_ids: list[int],
    ) -> dict[str, Any]:
        """Register additional images into an existing reconstruction."""

    def pose_graph_optimize(
        self, *, model_path: Path, output_path: Path, spec: dict[str, Any]
    ) -> dict[str, Any]:
        """Run pose-graph optimization."""


@runtime_checkable
class ExportBackend(Backend, Protocol):
    """Portable reconstruction export stages."""

    def export(self, *, model_path: Path, output_path: Path, format: str) -> dict[str, Any]:
        """Export a sparse model."""


@runtime_checkable
class SphericalBackend(Backend, Protocol):
    """Spherical/panorama conversion helpers."""

    def project_images(
        self,
        *,
        operation: str,
        input_image_path: Path,
        output_path: Path,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a portable image projection transform over a dataset."""

    def convert_spherical_to_cubemap(
        self,
        *,
        input_model_path: Path,
        input_image_path: Path,
        output_path: Path,
    ) -> dict[str, Any]:
        """Convert a spherical reconstruction to a cubemap rig."""

    def render_spherical_cubemap_images(
        self,
        *,
        input_image_path: Path,
        output_path: Path,
        face_size: int | None = None,
    ) -> dict[str, Any]:
        """Render every panorama into six cubemap face images."""


@runtime_checkable
class RetrievalBackend(Backend, Protocol):
    """Image retrieval and similarity helpers."""

    def build_vlad_index(
        self,
        *,
        image_paths_by_id: dict[str, Path],
        spec: dict[str, Any],
    ) -> tuple[list[str], Any]:
        """Compute VLAD descriptors for a set of images."""


@runtime_checkable
class VocabTreeBackend(Backend, Protocol):
    """Retrieval-index construction (capability ``index.vocab_tree``)."""

    def build_vocab_tree(
        self,
        *,
        database_path: Path,
        output_path: Path,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a reusable vocabulary-tree retrieval index from a feature DB."""


@runtime_checkable
class GeometryBackend(Backend, Protocol):
    """Standalone two-view geometry estimation (capability ``geometry.two_view``).

    Distinct from ``FeatureBackend.verify_matches`` (which filters an
    existing match set in place): this estimates relative geometry —
    essential / fundamental / homography matrices and relative pose —
    for an explicit set of image pairs.
    """

    def estimate_two_view_geometry(
        self,
        *,
        database_path: Path,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Estimate two-view geometry (E/F/H + relative pose) for image pairs."""


@runtime_checkable
class UndistortBackend(Backend, Protocol):
    """Image undistortion (capability ``image.undistort``).

    A portable sparse-SfM post-process: rewrite images to a distortion-free
    camera model and emit the adjusted intrinsics. NOT dense MVS — though
    it is commonly the first step of a dense pipeline.
    """

    def undistort_images(
        self,
        *,
        model_path: Path,
        image_root: Path,
        output_path: Path,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Undistort images + emit adjusted intrinsics into ``output_path``."""


@runtime_checkable
class RigBackend(Backend, Protocol):
    """Multi-camera rig declaration / calibration (capability ``rigs.configure``)."""

    def configure_rig(
        self,
        *,
        database_path: Path,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Declare or calibrate a multi-camera rig over a feature database."""


@runtime_checkable
class ArtifactConversionBackend(Backend, Protocol):
    """Convert one stage artifact format into another."""

    def convert_artifact(
        self,
        *,
        input_artifact: dict[str, Any],
        output_dir: Path,
        to_format: str,
        to_kind: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convert an artifact and return one or more artifact descriptors."""


@runtime_checkable
class LocalizationBackend(Backend, Protocol):
    """Single-image localization helpers."""

    def localize_from_memory(
        self, *, sparse_dir: Path, query_image: Path, spec: dict[str, Any]
    ) -> dict[str, Any]:
        """Localize a query image against a sparse model."""


@runtime_checkable
class BatchLocalizationBackend(Backend, Protocol):
    """Batch or sequence localization helpers."""

    def localize_batch(
        self,
        *,
        sparse_dir: Path,
        queries_path: Path,
        spec: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Localize every query listed in ``queries_path`` against a sparse model.

        The batch analog of :meth:`LocalizationBackend.localize_from_memory`:
        ``sparse_dir`` is the reference model, ``queries_path`` the file
        naming the query images, and ``spec`` carries engine-specific
        inputs (e.g. hloc's ``retrieval_path`` / ``feature_path`` /
        ``matches_path`` and solver knobs). Returns one result row per
        localization pass, each a JSON-serializable dict of output paths
        + engine metadata.
        """


@runtime_checkable
class TransformBackend(Backend, Protocol):
    """Geometry transform helpers."""

    def apply_sim3(
        self,
        *,
        model_path: Path,
        output_path: Path,
        sim3: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply a Sim(3) similarity transform to a sparse model."""

    def align_reconstruction(
        self,
        *,
        model_path: Path,
        output_path: Path,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Estimate + apply georegistration from GPS / geo-tags / control points.

        Capability ``georegister.gps``. Unlike :meth:`apply_sim3` (which
        applies a caller-supplied transform), this *solves* the transform
        from georeferenced inputs declared in ``spec``.
        """


@runtime_checkable
class ReconstructionReaderBackend(Backend, Protocol):
    """Read a sparse model into a snapshot-emitter-compatible object."""

    def read_reconstruction(self, path: Path) -> Any:
        """Return a duck-typed reconstruction object."""


@runtime_checkable
class ReconstructionMergeBackend(Backend, Protocol):
    """Multi-reconstruction merge helpers."""

    def merge_reconstructions(
        self,
        *,
        model_paths: list[Path],
        output_path: Path,
        sim3_aligners: Any = None,
    ) -> dict[str, Any]:
        """Merge multiple sparse models into one output model."""


@runtime_checkable
class SfmBackend(
    FeatureBackend,
    ObservationBackend,
    GeometryBackend,
    MappingBackend,
    RefinementBackend,
    ExportBackend,
    SphericalBackend,
    RetrievalBackend,
    VocabTreeBackend,
    LocalizationBackend,
    BatchLocalizationBackend,
    TransformBackend,
    UndistortBackend,
    RigBackend,
    ReconstructionReaderBackend,
    ReconstructionMergeBackend,
    ArtifactConversionBackend,
    Protocol,
):
    """Full portable SfM backend protocol kept for complete engines.

    New backend packages should usually target the smallest protocol
    layer they actually implement and expose native tools through
    backend actions where no portable stage contract exists.
    """


def has_backend_method(backend: object, method_name: str) -> bool:
    """Return whether ``backend`` exposes a callable method."""

    return callable(getattr(backend, method_name, None))


def require_backend_method(
    backend: object,
    method_name: str,
    *,
    capability: str,
    reason: str | None = None,
) -> Callable[..., Any]:
    """Return a backend method or raise a clean capability error.

    This is the guard portable workers use before calling optional
    protocol surfaces. It lets action-only or artifact-only backends
    omit unsupported methods entirely while preserving sfmapi's normal
    501 response semantics.
    """

    method = getattr(backend, method_name, None)
    if callable(method):
        return cast(Callable[..., Any], method)
    backend_name = getattr(backend, "name", backend.__class__.__name__)
    detail = reason or f"Backend {backend_name!r} does not implement {method_name}()."
    raise CapabilityUnavailableError(capability=capability, reason=detail)


__all__ = [
    "ArtifactConversionBackend",
    "Backend",
    "BackendIdentity",
    "BatchLocalizationBackend",
    "ExportBackend",
    "FeatureBackend",
    "GeometryBackend",
    "LocalizationBackend",
    "MappingBackend",
    "ObservationBackend",
    "ProgressReporter",
    "ReconstructionMergeBackend",
    "ReconstructionReaderBackend",
    "RefinementBackend",
    "RetrievalBackend",
    "RigBackend",
    "SfmBackend",
    "SphericalBackend",
    "TransformBackend",
    "UndistortBackend",
    "VocabTreeBackend",
    "has_backend_method",
    "require_backend_method",
]
