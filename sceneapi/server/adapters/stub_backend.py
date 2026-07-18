"""Stub full backend used by tests + ephemeral mode.

sfmapi ships no real SfM engine; the wire surface and orchestration
shell live in this repo, while engine implementations (pycolmap,
OpenSfM, hloc, custom forks) live in separate packages. This module
provides a no-op stub that satisfies the protocol structurally —
useful for:

* unit / integration / contract tests that exercise routing,
  scheduling, snapshot writing, etc., without needing a real engine,
* the ``SCENEAPI_EPHEMERAL=true`` self-contained demo runtime, which
  registers it on lifespan startup,
* SDK live tests that boot the app in a subprocess.

Most operations raise :class:`CapabilityUnavailableError` — only
``capabilities()`` and trivial probes return real values. The ONE
substantive surface is the sceneapi-io ``Mapper`` contract
(``traits()`` / ``map()``): the stub is a feed-forward mapper that
returns a tiny deterministic ``MappingResult`` (first view registered,
the rest honestly unregistered, a fixed 8-point cloud, one tiny dense
payload) so the feed-forward recipe is end-to-end testable without an
engine. Production deployments register a real backend via
:func:`sceneapi.server.adapters.registry.register_backend`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from sceneapi.server.core.errors import CapabilityUnavailableError


class StubBackend:
    """No-op stub satisfying the full ``SfmBackend`` union (plus the
    sceneapi-io ``Mapper`` contract)."""

    name = "stub"
    version = "0.0.1"
    vendor = "test"

    def capabilities(self) -> set[str]:
        # The stub advertises no v0 engine capabilities. Its ONE
        # advertised capability follows contract presence: it claims
        # ``map.feed_forward`` because (and only because) this object
        # satisfies the sceneapi-io Mapper contract with feed-forward
        # traits — the same io-Mapper presence the dual-dispatching map
        # worker keys on. Tests that need other methods to succeed
        # should subclass + override.
        from sceneapi_io.mapping import Mapper, MapperTraits

        caps: set[str] = set()
        if isinstance(self, Mapper):
            traits = self.traits()
            if isinstance(traits, MapperTraits) and not traits.requires_correspondences:
                caps.add("map.feed_forward")
        return caps

    # ---- sceneapi-io procedure contracts --------------------------------

    def traits(self) -> Any:
        """Feed-forward MapperTraits (sceneapi-io Mapper contract)."""
        from sceneapi_io.mapping import MapperTraits

        return MapperTraits(
            requires_correspondences=False,
            accepts_pose_priors=True,
            accepts_depth_priors=False,
            accepts_calibration=False,
            emits_dense=True,
            metric_capable=False,
        )

    def map(
        self,
        views: Sequence[Any],
        *,
        correspondences: Any = None,
        options: Any = None,
    ) -> Any:
        """A tiny valid feed-forward ``MappingResult``.

        The first view registers at the identity pose with a small
        dense payload; every other view is honestly unregistered
        (``None`` pose / dense entries per the 0.2.x amendment). The
        sparse geometry is a fixed 8-corner unit cube so snapshot
        plumbing has real points to serve. Never reads image pixels.
        """
        import numpy as np
        from sceneapi_io.data import SE3, ConfidenceMap, FrameMeta, Pointmap, TrackedPointCloud
        from sceneapi_io.mapping import MappingResult

        if not views:
            raise CapabilityUnavailableError(
                capability="map.feed_forward", reason="stub mapper needs at least one view"
            )
        poses: list[Any] = [None] * len(views)
        poses[0] = SE3.identity()
        dense: list[Any] = [None] * len(views)
        dense[0] = (
            Pointmap(points=np.zeros((2, 2, 3), dtype=np.float32)),
            ConfidenceMap(values=np.full((2, 2), 0.5, dtype=np.float32)),
        )
        corners = np.array(
            [[x, y, z] for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.0, 1.0)],
            dtype=np.float32,
        )
        rgb = (np.arange(corners.size, dtype=np.int64) % 256).reshape(corners.shape)
        geometry = TrackedPointCloud(xyz=corners, rgb=rgb.astype(np.uint8))
        return MappingResult(
            poses=tuple(poses),
            frame=FrameMeta(scale="arbitrary", scale_provenance="unknown"),
            geometry=geometry,
            dense=tuple(dense),
            stats={"num_views": len(views), "stub": True},
        )

    def extract_features(
        self,
        *,
        database_path: Path,
        image_root: Path,
        image_list: list[str],
        options: dict,
    ) -> dict:
        raise CapabilityUnavailableError(capability="features.extract")

    def match(self, *, database_path: Path, mode: str, options: dict) -> dict:
        raise CapabilityUnavailableError(capability=f"pairs.{mode}")

    def verify_matches(self, *, database_path: Path, options: dict) -> dict:
        raise CapabilityUnavailableError(capability="matches.verify")

    def read_keypoints(self, **_: Any) -> tuple[list[list[float]], bytes, int]:
        raise CapabilityUnavailableError(capability="features.extract")

    def iter_two_view_geometries(self, *, database_path: Path) -> Iterator:
        return iter([])

    def iter_correspondences(self, *, database_path: Path) -> Iterator:
        return iter([])

    def run_mapping(self, **_: Any) -> tuple[list[dict], list[Any]]:
        raise CapabilityUnavailableError(capability="map.incremental")

    def bundle_adjustment(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="ba.standard")

    def triangulate(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="triangulate.retri")

    def relocalize(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="relocalize.images")

    def pose_graph_optimize(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="pgo.optimize")

    def export(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="export.ply")

    def convert_spherical_to_cubemap(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="projection.cubemap_rig")

    def project_images(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="projection.equirectangular_to_cubemap")

    def render_spherical_cubemap_images(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="projection.equirectangular_to_cubemap")

    def build_vlad_index(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="similarity.vlad")

    def convert_artifact(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="artifacts.convert")

    def localize_from_memory(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="localize.from_memory")

    def apply_sim3(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="georegister.sim3")

    def align_reconstruction(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="georegister.gps")

    def estimate_two_view_geometry(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="geometry.two_view")

    def undistort_images(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="image.undistort")

    def build_vocab_tree(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="index.vocab_tree")

    def configure_rig(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="rigs.configure")

    def merge_reconstructions(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="recon.merge")

    def localize_batch(self, **_: Any) -> list:
        raise CapabilityUnavailableError(capability="localize.batch")

    def read_reconstruction(self, path: Path) -> Any:
        raise CapabilityUnavailableError(capability="features.extract")

    def runtime_versions(self) -> dict[str, str]:
        return {"stub_version": "0.0.1"}
