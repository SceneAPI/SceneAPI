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
``capabilities()`` and trivial probes return real values. Production
deployments register a real backend via
:func:`sceneapi.server.adapters.registry.register_backend`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sceneapi.server.core.errors import CapabilityUnavailableError


class StubBackend:
    """No-op stub satisfying the full ``SfmBackend`` union."""

    name = "stub"
    version = "0.0.1"
    vendor = "test"

    def capabilities(self) -> set[str]:
        # Stub advertises no capabilities. Tests that need a method
        # to succeed should subclass + override, or register their
        # own backend with the desired capability set.
        return set()

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
