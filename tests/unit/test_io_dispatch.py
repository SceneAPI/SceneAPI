"""Dual-dispatch resolvers + stub io-Mapper conformance (P8 Steps 5-6).

The map/extract/match/verify workers prefer a backend implementing the
sceneapi-io Protocols over the v0 Path-protocols. These tests pin the
resolver semantics (including the traits-TYPE guard that keeps the
structural Protocols from misrouting a Mapper into the matching path),
prove the StubBackend passes the sceneapi-io mapper conformance kit,
and exercise the extract/match/verify dual dispatch: an io conformer
now drives the real io path (Step 6), while a v0-only backend still
runs the v0 protocol byte-for-byte unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sceneapi_io.data import FeatureSet, PairCorrespondences, TwoViewGeometry
from sceneapi_io.matching import MatcherTraits
from sceneapi_io.testing import assert_mapper_conformance

from sceneapi.server.adapters.registry import register_backend
from sceneapi.server.adapters.stub_backend import StubBackend
from sceneapi.server.core.capabilities import reset_capabilities_cache
from sceneapi.server.core.errors import CapabilityUnavailableError
from sceneapi.server.core.ids import new_id
from sceneapi.server.db.models import Task
from sceneapi.server.workers import _io_match
from sceneapi.server.workers._io_dispatch import (
    io_feature_extractor,
    io_geometric_verifier,
    io_mapper,
    io_pair_matcher,
)

pytestmark = pytest.mark.unit


# ---- resolver semantics ---------------------------------------------------


class _MatcherTwin:
    """Standalone sceneapi-io PairMatcher (MatcherTraits + match_pair)."""

    name = "matcher_twin"
    version = "0.0"
    vendor = "test"

    def capabilities(self) -> set[str]:
        return set()

    def runtime_versions(self) -> dict[str, str]:
        return {}

    def traits(self) -> MatcherTraits:
        return MatcherTraits(persistent_keypoints=True, detector_free=False)

    def match_pair(self, a: Any, b: Any, *, options: Any = None) -> Any:
        raise NotImplementedError

    # a `map` method too — the resolver must still NOT treat this
    # MatcherTraits-bearing object as a Mapper.
    def map(self, views: Any, *, correspondences: Any = None, options: Any = None) -> Any:
        raise NotImplementedError


class _ExtractorTwin:
    def extract(self, image: Any, *, options: Any = None) -> Any:
        raise NotImplementedError


class _VerifierTwin:
    def verify(self, pair: Any, *, options: Any = None) -> Any:
        raise NotImplementedError


class _BrokenTraits(StubBackend):
    def traits(self) -> Any:  # type: ignore[override]
        raise RuntimeError("boom")


def test_stub_backend_resolves_as_io_mapper() -> None:
    stub = StubBackend()
    assert io_mapper(stub) is stub


def test_stub_backend_is_not_misrouted_into_matching() -> None:
    # StubBackend has traits() (MapperTraits) but no match_pair/extract/
    # verify — none of the matching-side resolvers may claim it.
    stub = StubBackend()
    assert io_pair_matcher(stub) is None
    assert io_feature_extractor(stub) is None
    assert io_geometric_verifier(stub) is None


def test_matcher_twin_resolves_as_pair_matcher_not_mapper() -> None:
    twin = _MatcherTwin()
    assert io_pair_matcher(twin) is twin
    # Structurally `map` + `traits` satisfy the Mapper Protocol, but the
    # traits TYPE is MatcherTraits — the resolver must refuse.
    assert io_mapper(twin) is None


def test_mapper_traits_object_with_match_pair_is_not_a_pair_matcher() -> None:
    class MapperWithMatchPair(StubBackend):
        def match_pair(self, a: Any, b: Any, *, options: Any = None) -> Any:
            raise NotImplementedError

    twin = MapperWithMatchPair()
    assert io_mapper(twin) is twin
    assert io_pair_matcher(twin) is None  # traits() returns MapperTraits


def test_extractor_and_verifier_twins_resolve() -> None:
    extractor = _ExtractorTwin()
    verifier = _VerifierTwin()
    assert io_feature_extractor(extractor) is extractor
    assert io_geometric_verifier(verifier) is verifier
    assert io_mapper(extractor) is None
    assert io_pair_matcher(verifier) is None


def test_broken_traits_never_breaks_dispatch() -> None:
    broken = _BrokenTraits()
    assert io_mapper(broken) is None
    assert io_pair_matcher(broken) is None


# ---- StubBackend io-Mapper honesty ----------------------------------------


def test_stub_backend_passes_mapper_conformance_kit() -> None:
    result = assert_mapper_conformance(StubBackend())
    # tiny result: first view registered, the rest honestly unregistered
    assert result.poses[0] is not None
    assert all(pose is None for pose in result.poses[1:])
    assert result.geometry is not None
    assert len(result.geometry) == 8
    assert result.dense is not None
    assert result.dense[0] is not None


def test_stub_advertises_feed_forward_via_io_mapper_presence() -> None:
    assert StubBackend().capabilities() == {"map.feed_forward"}

    # capability follows contract presence: a classical-traits mapper
    # has no feed-forward surface to advertise
    class RequiresCorrespondencesStub(StubBackend):
        def traits(self) -> Any:
            from sceneapi_io.mapping import MapperTraits

            return MapperTraits(
                requires_correspondences=True,
                accepts_pose_priors=False,
                accepts_depth_priors=False,
                accepts_calibration=False,
                emits_dense=False,
                metric_capable=False,
            )

    assert RequiresCorrespondencesStub().capabilities() == set()


def test_capabilities_endpoint_reports_feed_forward_for_stub() -> None:
    from sceneapi.server.core.capabilities import detect_capabilities

    reset_capabilities_cache()
    caps = detect_capabilities()
    assert caps.supports("map.feed_forward")


# ---- task-level dual dispatch (StubBackend twins) -------------------------


def _stage_task(kind: str, inputs: dict[str, Any], spec: dict[str, Any]) -> Task:
    return Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=new_id(),
        kind=kind,
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        task_state_json={"inputs": inputs, "spec": spec},
    )


def _local_images(tmp_path: Path, names: list[str]) -> dict[str, Any]:
    img_dir = tmp_path / "imgs"
    img_dir.mkdir(exist_ok=True)
    for name in names:
        (img_dir / name).write_bytes(b"\xff\xd8\xff\xe0" + name.encode())
    return {"kind": "local", "image_list": names, "image_root": str(img_dir)}


def _use_backend(backend_cls: type) -> None:
    register_backend("stub", backend_cls)
    reset_capabilities_cache()


def _feature_set(n: int = 2) -> FeatureSet:
    keypoints = np.arange(2 * n, dtype=np.float32).reshape(n, 2)
    return FeatureSet(keypoints=keypoints)


def test_extract_task_runs_io_path_when_io_extractor_present(tmp_path: Path) -> None:
    """Step 6: an io FeatureExtractor drives the io path; v0 is not called."""
    from sceneapi.server.workers.tasks import extract as extract_task

    v0_calls: list[str] = []

    class DualExtractBackend(StubBackend):
        def extract(self, image: Any, *, options: Any = None) -> Any:
            return _feature_set()

        def extract_features(self, **kwargs: Any) -> dict[str, Any]:
            v0_calls.append("v0")
            return {"num_images": 0}

    _use_backend(DualExtractBackend)
    task = _stage_task(
        "extract",
        {
            "project_id": "p1",
            "recon_id": "r1",
            "database_path": str(tmp_path / "database.db"),
            "materialization": _local_images(tmp_path, ["a.jpg", "b.jpg"]),
        },
        {"type": "sift"},
    )
    out = extract_task.run(task)
    assert v0_calls == []  # io path preferred over v0 extract_features
    assert out["num_images"] == 2
    store = _io_match.correspondence_store_root(Path(out["database_path"]))
    assert sorted(_io_match.load_feature_sets(store)) == ["a.jpg", "b.jpg"]


def test_extract_task_v0_only_backend_keeps_501_semantics(tmp_path: Path) -> None:
    """A backend with no io FeatureExtractor still 501s through v0 unchanged."""
    from sceneapi.server.workers.tasks import extract as extract_task

    _use_backend(StubBackend)  # no `extract`; extract_features raises 501
    task = _stage_task(
        "extract",
        {
            "project_id": "p1",
            "recon_id": "r1",
            "database_path": str(tmp_path / "database.db"),
            "materialization": _local_images(tmp_path, ["a.jpg"]),
        },
        {"type": "sift"},
    )
    with pytest.raises(CapabilityUnavailableError):
        extract_task.run(task)


def test_match_task_runs_io_path_when_io_matcher_present(tmp_path: Path) -> None:
    """Step 6: a detector-based io PairMatcher pairs the stored FeatureSets."""
    from sceneapi.server.workers.tasks import match as match_task

    db_path = tmp_path / "database.db"
    store = _io_match.correspondence_store_root(db_path)
    for name in ("a.jpg", "b.jpg"):
        _io_match.write_feature_set(store, name, _feature_set())

    v0_calls: list[str] = []

    class IoMatchBackend(StubBackend):
        def traits(self) -> MatcherTraits:  # type: ignore[override]
            return MatcherTraits(persistent_keypoints=True, detector_free=False)

        def match_pair(self, a: Any, b: Any, *, options: Any = None) -> Any:
            return PairCorrespondences.from_indices(np.array([[0, 1]], dtype=np.int64))

        def match(self, *, database_path: Path, mode: str, options: dict) -> dict:
            v0_calls.append("v0")
            return {"num_matched_pairs": 0}

    _use_backend(IoMatchBackend)
    task = _stage_task(
        "match",
        {"recon_id": "r1", "dataset_id": "d1", "database_path": str(db_path)},
        {"pairs": {"strategy": "exhaustive"}, "matcher": {"type": "nn-mutual"}},
    )
    out = match_task.run(task)
    assert v0_calls == []  # io path preferred over v0 match
    assert out["strategy"] == "exhaustive"
    assert out["num_matched_pairs"] == 1
    assert set(_io_match.load_pair_correspondences(store)) == {("a.jpg", "b.jpg")}


def test_verify_task_runs_io_path_when_io_verifier_present(tmp_path: Path) -> None:
    """Step 6: an io GeometricVerifier filters the stored pairs + attaches geometry."""
    from sceneapi.server.workers.tasks import verify as verify_task

    db_path = tmp_path / "database.db"
    store = _io_match.correspondence_store_root(db_path)
    _io_match.write_pair_correspondences(
        store,
        "a.jpg",
        "b.jpg",
        PairCorrespondences.from_indices(np.array([[0, 0], [1, 1]], dtype=np.int64)),
    )

    v0_calls: list[str] = []

    class IoVerifyBackend(StubBackend):
        def verify(self, pair: Any, *, options: Any = None) -> Any:
            return PairCorrespondences.from_indices(
                pair.indices[:1], geometry=TwoViewGeometry(E=np.eye(3), num_inliers=1)
            )

        def verify_matches(self, *, database_path: Path, options: dict) -> dict:
            v0_calls.append("v0")
            return {"num_verified_pairs": 0}

    _use_backend(IoVerifyBackend)
    task = _stage_task(
        "verify",
        {"recon_id": "r1", "dataset_id": "d1", "database_path": str(db_path)},
        {},
    )
    out = verify_task.run(task)
    assert v0_calls == []  # io path preferred over v0 verify_matches
    assert out["num_verified_pairs"] == 1
    verified = _io_match.load_pair_correspondences(store)  # verified/ overrides matches/
    assert verified[("a.jpg", "b.jpg")].geometry is not None
    assert len(verified[("a.jpg", "b.jpg")]) == 1


def test_match_task_v0_fallback_unchanged(tmp_path: Path) -> None:
    """A backend with no io PairMatcher still runs v0 match untouched."""
    from sceneapi.server.workers.tasks import match as match_task

    calls: list[str] = []

    class V0MatchBackend(StubBackend):
        def match(self, *, database_path: Path, mode: str, options: dict) -> dict:
            calls.append(mode)
            return {"num_matched_pairs": 0}

    _use_backend(V0MatchBackend)
    task = _stage_task(
        "match",
        {"recon_id": "r1", "dataset_id": "d1", "database_path": str(tmp_path / "db.db")},
        {"pairs": {"strategy": "exhaustive"}, "matcher": {"type": "nn-mutual"}},
    )
    out = match_task.run(task)
    assert calls == ["exhaustive"]
    assert out["strategy"] == "exhaustive"


def test_verify_task_v0_fallback_unchanged(tmp_path: Path) -> None:
    """A backend with no io GeometricVerifier still runs v0 verify untouched."""
    from sceneapi.server.workers.tasks import verify as verify_task

    calls: list[str] = []

    class V0VerifyBackend(StubBackend):
        def verify_matches(self, *, database_path: Path, options: dict) -> dict:
            calls.append("v0")
            return {"num_verified_pairs": 0}

    _use_backend(V0VerifyBackend)
    task = _stage_task(
        "verify",
        {"recon_id": "r1", "dataset_id": "d1", "database_path": str(tmp_path / "db.db")},
        {},
    )
    out = verify_task.run(task)
    assert calls == ["v0"]
    assert out["num_verified_pairs"] == 0
