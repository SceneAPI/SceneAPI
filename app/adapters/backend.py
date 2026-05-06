"""``SfmBackend`` — the contract every backend implementation honors.

sfmapi is a wire standard with multiple possible engines. This module
declares the **single Python-side contract** that worker tasks call
through. Workers **MUST NOT** import an engine library
(``pycolmap``, ``openmvg``, ...) directly — they call
``get_backend()`` and use the returned backend's methods.

A new backend is added by:

1. Implementing this :class:`SfmBackend` protocol (any subset of
   methods; unsupported operations **MUST** raise
   :class:`app.core.errors.CapabilityUnavailableError`).
2. Registering a factory under a canonical short name via
   :func:`app.adapters.registry.register_backend`.
3. Implementing :meth:`SfmBackend.capabilities` so it returns the set
   of canonical capability names the backend exposes (subset of
   :data:`app.core.capabilities.ALL_KNOWN`).

The colmap_mod backend lives in :mod:`app.adapters.colmap_backend`
and is the default. Adding e.g. an OpenMVG backend is purely an
additive change — no protocol method gets renamed when a new engine
joins.

Method return shapes are documented as plain dicts; clients that need
strict typing should validate against the corresponding schema in
:mod:`app.schemas.api.scene`. Returning extra fields is allowed —
callers MUST tolerate them.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SfmBackend(Protocol):
    """Structural-typing contract for an SfM backend."""

    # ---- identity --------------------------------------------------------

    @property
    def name(self) -> str:
        """Canonical short name (e.g. ``"colmap_mod"``)."""

    @property
    def version(self) -> str:
        """Backend version string (free form)."""

    @property
    def vendor(self) -> str:
        """Optional human-readable vendor / origin."""

    def capabilities(self) -> set[str]:
        """Set of canonical capability names this backend exposes.

        Used by :func:`app.core.capabilities.detect_capabilities` to
        flip OPTIONAL flags ``True``. Returning a name here means the
        corresponding method on this backend will succeed when called
        with valid inputs."""

    # ---- feature pipeline ----------------------------------------------

    def extract_features(
        self,
        *,
        database_path: Path,
        image_root: Path,
        image_list: list[str],
        options: dict,
    ) -> dict:
        """Run feature extraction over ``image_list`` into the SfM
        database. Returns ``{num_images, num_keypoints, ...}``."""

    def match(self, *, database_path: Path, mode: str, options: dict) -> dict:
        """Run feature matching. ``mode`` is one of
        ``exhaustive | sequential | spatial | vocabtree``. Returns
        ``{num_matches, ...}``."""

    def verify_matches(self, *, database_path: Path, options: dict) -> dict:
        """Run geometric verification on existing matches. Returns
        ``{num_verified_matches, ...}``."""

    # ---- inspector helpers (for the export sidecars) -------------------

    def iter_two_view_geometries(self, *, database_path: Path) -> Iterator[tuple[int, int, Any]]:
        """Walk every image pair in the database that has a verified
        two-view geometry. Yields ``(image_id1, image_id2, geometry)``
        where ``geometry`` is a duck-typed object with ``F``/``E``/``H``
        attributes (see :mod:`app.storage.two_view_emit`)."""

    def iter_correspondences(self, *, database_path: Path) -> Iterator[tuple[int, int, Any]]:
        """Walk every image pair with raw matches. Yields
        ``(image_id1, image_id2, matches)`` where ``matches`` is
        iterable over ``(kp_idx_1, kp_idx_2)`` pairs (see
        :mod:`app.storage.correspondence_emit`)."""

    # ---- mapping --------------------------------------------------------

    def run_mapping(
        self,
        *,
        kind: str,
        db_path: Path,
        image_root: Path,
        sparse_root: Path,
        job_dir: Path,
        spec: dict,
        pose_priors: dict | None = None,
    ) -> tuple[list[dict], list[Any]]:
        """Run the named mapping pipeline. ``kind`` is
        ``incremental | global | hierarchical | spherical``. Returns
        ``(summaries, reconstructions)`` — each summary is a dict
        (``{idx, num_reg_images, num_points3D}``) and each
        reconstruction is a duck-typed object the snapshot emitter
        can consume (see :mod:`app.storage.snapshot_emit`)."""

    # ---- refinement -----------------------------------------------------

    def bundle_adjustment(self, *, model_path: Path, output_path: Path, spec: dict) -> dict:
        """Run BA. ``spec.mode`` is ``standard`` (default) or
        ``two_stage`` — the latter requires capability
        ``ba.two_stage``. Returns
        ``{model_path, mode, num_reg_images, num_points3D}``."""

    def triangulate(
        self,
        *,
        model_path: Path,
        database_path: Path,
        image_root: Path,
        output_path: Path,
    ) -> dict:
        """Re-triangulate against an existing database."""

    def relocalize(
        self,
        *,
        model_path: Path,
        database_path: Path,
        image_root: Path,
        output_path: Path,
        image_ids: list[int],
    ) -> dict:
        """Register additional images into an existing reconstruction."""

    def pose_graph_optimize(self, *, model_path: Path, output_path: Path, spec: dict) -> dict:
        """Run pose-graph optimization."""

    # ---- output / conversion -------------------------------------------

    def export(self, *, model_path: Path, output_path: Path, format: str) -> dict:
        """Export a sparse model. ``format`` is one of
        ``ply | nvm | colmap_text | colmap_bin | nerfstudio |
        gaussian_splatting | instant_ngp | kapture``."""

    def generate_mesh(
        self,
        *,
        sparse_dir: Path,
        dense_fused_path: Path | None,
        output_path: Path,
        method: str,
        options: dict,
    ) -> dict:
        """Generate a mesh from a reconstruction. ``method`` is one of
        ``poisson | delaunay``. ``dense_fused_path`` is optional —
        Poisson typically wants the dense fused cloud; Delaunay can
        run on the sparse model. Returns a
        :class:`app.schemas.api.scene.MeshSummary`-shaped dict."""

    def convert_spherical_to_cubemap(
        self,
        *,
        input_model_path: Path,
        input_image_path: Path,
        output_path: Path,
    ) -> dict:
        """Convert a spherical reconstruction to a cubemap rig."""

    def render_spherical_cubemap_images(
        self,
        *,
        input_image_path: Path,
        output_path: Path,
        face_size: int | None = None,
    ) -> dict:
        """Render every panorama into 6 face images."""

    # ---- dense MVS ------------------------------------------------------

    def dense_pipeline(
        self,
        *,
        sparse_dir: Path,
        image_root: Path,
        workspace: Path,
        out_dir: Path,
        spec: dict,
    ) -> dict:
        """Run dense MVS end-to-end and write sfmapi wire-format
        outputs into ``out_dir``.

        Backend-side responsibilities:
          - Run undistort + patch_match_stereo + stereo_fusion (or
            equivalent for non-COLMAP backends).
          - Emit ``cameras.json`` / ``images.json`` / ``points.bin`` /
            ... at ``out_dir`` (sparse-model snapshot emit).
          - Convert per-image depth/normal maps to
            ``application/x-sfm-depth-v1`` / ``application/x-sfm-normal-v1``
            under ``out_dir/dense/depth_maps/`` and
            ``out_dir/dense/normal_maps/``.
          - Convert the fused dense cloud to
            ``application/x-sfm-points-v1`` at ``out_dir/dense/fused.bin``.
          - Write ``out_dir/dense/index.json`` (DenseManifestFile).

        Returns ``{num_depth_maps, fused_points}`` for the worker to
        propagate into the sealed snapshot summary."""

    # ---- retrieval ------------------------------------------------------

    def build_vlad_index(
        self,
        *,
        image_paths_by_id: dict[str, Path],
        spec: dict,
    ) -> tuple[list[str], Any]:
        """Compute VLAD descriptors for the given images. Returns
        ``(image_ids, vectors)`` where ``vectors`` is a NumPy array of
        shape ``(N, D)`` parallel to ``image_ids``. The caller persists
        through :func:`app.storage.vlad.write_index`."""

    # ---- localization ---------------------------------------------------

    def localize_from_memory(self, *, sparse_dir: Path, query_image: Path, spec: dict) -> dict:
        """Localize a single query image against the sparse model.
        Returns a :class:`app.schemas.api.scene.LocalizationResult`-
        shaped dict."""

    # ---- geometry transforms -------------------------------------------

    def apply_sim3(
        self,
        *,
        model_path: Path,
        output_path: Path,
        sim3: dict,
    ) -> dict:
        """Apply a Sim(3) similarity transform to a sparse model and
        write the result. ``sim3`` is the wire shape (rotation as
        Hamilton ``wxyz``, translation 3-vec, scale float)."""

    # ---- low-level: load a sparse model into memory --------------------

    def read_reconstruction(self, path: Path) -> Any:
        """Return a duck-typed reconstruction object the snapshot
        emitter (:mod:`app.storage.snapshot_emit`) can consume."""

    # ---- runtime version vector ----------------------------------------

    def runtime_versions(self) -> dict[str, str]:
        """Return engine + dependency versions for the cache key."""


__all__ = ["SfmBackend"]
