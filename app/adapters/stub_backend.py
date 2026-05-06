"""Stub :class:`SfmBackend` used by tests + ephemeral mode.

sfmapi ships no real SfM engine; the wire surface and orchestration
shell live in this repo, while engine implementations (pycolmap,
OpenSfM, hloc, custom forks) live in separate packages. This module
provides a no-op stub that satisfies the protocol structurally —
useful for:

* unit / integration / contract tests that exercise routing,
  scheduling, snapshot writing, etc., without needing a real engine,
* the ``SFMAPI_EPHEMERAL=true`` self-contained demo runtime, which
  registers it on lifespan startup,
* SDK live tests that boot the app in a subprocess.

Most operations raise :class:`CapabilityUnavailableError` — only
``capabilities()`` and trivial probes return real values. Production
deployments register a real backend via
:func:`app.adapters.registry.register_backend`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.core.errors import CapabilityUnavailableError


class StubBackend:
    """Minimal stub satisfying SfmBackend by structural typing."""

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
        raise CapabilityUnavailableError(capability=f"matches.{mode}")

    def verify_matches(self, *, database_path: Path, options: dict) -> dict:
        raise CapabilityUnavailableError(capability="matches.verify")

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
        raise CapabilityUnavailableError(capability="spherical.to_cubemap")

    def render_spherical_cubemap_images(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="spherical.render_cubemap")

    def dense_pipeline(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="dense.patch_match_stereo")

    def build_vlad_index(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="similarity.vlad")

    def localize_from_memory(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="localize.from_memory")

    def apply_sim3(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="georegister.sim3")

    def generate_mesh(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="mesh.poisson")

    def merge_reconstructions(self, **_: Any) -> dict:
        raise CapabilityUnavailableError(capability="recon.merge")

    def localize_batch(self, **_: Any) -> list:
        raise CapabilityUnavailableError(capability="localize.batch")

    def read_reconstruction(self, path: Path) -> Any:
        raise CapabilityUnavailableError(capability="features.extract")

    def runtime_versions(self) -> dict[str, str]:
        return {"stub_version": "0.0.1"}
