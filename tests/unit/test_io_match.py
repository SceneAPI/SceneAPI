"""The sceneapi-io matching bridge (P8 Step 6).

Covers the writers/readers between the sceneapi-io sparse-correspondence
types and the sealed on-disk store (round-trip equal within dtype), the
``CorrespondenceGraph`` reader that feeds a classical io ``Mapper``,
downstream-compat (an io feature stage's output threads into a v0 match
stage at the fixture level), and an e2e-lite pipeline that drives
features -> matches -> verify -> map entirely through the io Protocols
with sealed snapshot output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest
from sceneapi_io.data import (
    SE3,
    FeatureSet,
    FrameMeta,
    PairCorrespondences,
    TwoViewGeometry,
)
from sceneapi_io.mapping import MapperTraits, MappingResult
from sceneapi_io.matching import MatcherTraits

from sceneapi.server.adapters.registry import register_backend
from sceneapi.server.adapters.stub_backend import StubBackend
from sceneapi.server.core.capabilities import reset_capabilities_cache
from sceneapi.server.core.ids import new_id
from sceneapi.server.db.models import Task
from sceneapi.server.workers import _io_match

pytestmark = pytest.mark.unit


# ---- FeatureSet round-trip -------------------------------------------------


class TestFeatureSetRoundTrip:
    def test_full_feature_set_equal_within_dtype(self, tmp_path: Path) -> None:
        feature_set = FeatureSet(
            keypoints=np.array([[1.5, 2.5], [3.5, 4.5], [5.5, 6.5]], dtype=np.float32),
            descriptors=np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.uint8),
            scores=np.array([0.1, 0.2, 0.3], dtype=np.float32),
        )
        _io_match.write_feature_set(tmp_path, "sub dir/a.jpg", feature_set)
        loaded = _io_match.load_feature_sets(tmp_path)
        assert set(loaded) == {"sub dir/a.jpg"}
        got = loaded["sub dir/a.jpg"]
        np.testing.assert_array_equal(got.keypoints, feature_set.keypoints)
        assert got.keypoints.dtype == np.float32
        assert got.descriptors is not None
        np.testing.assert_array_equal(got.descriptors, feature_set.descriptors)
        assert got.descriptors.dtype == np.uint8  # descriptor dtype preserved
        assert got.scores is not None
        np.testing.assert_array_equal(got.scores, feature_set.scores)

    def test_keypoints_only_feature_set(self, tmp_path: Path) -> None:
        feature_set = FeatureSet(keypoints=np.array([[0.0, 0.0]], dtype=np.float32))
        _io_match.write_feature_set(tmp_path, "a.jpg", feature_set)
        got = _io_match.load_feature_sets(tmp_path)["a.jpg"]
        assert got.descriptors is None
        assert got.scores is None
        np.testing.assert_array_equal(got.keypoints, feature_set.keypoints)

    def test_float_descriptors_preserve_float_dtype(self, tmp_path: Path) -> None:
        feature_set = FeatureSet(
            keypoints=np.zeros((2, 2), dtype=np.float32),
            descriptors=np.array([[0.5, 1.5], [2.5, 3.5]], dtype=np.float32),
        )
        _io_match.write_feature_set(tmp_path, "a.jpg", feature_set)
        got = _io_match.load_feature_sets(tmp_path)["a.jpg"]
        assert got.descriptors is not None
        assert got.descriptors.dtype == np.float32
        np.testing.assert_array_equal(got.descriptors, feature_set.descriptors)


# ---- PairCorrespondences round-trip ---------------------------------------


class TestPairCorrespondencesRoundTrip:
    def test_indexed_pair_preserves_index_dtype(self, tmp_path: Path) -> None:
        pair = PairCorrespondences.from_indices(
            np.array([[0, 1], [2, 3]], dtype=np.int32),
            scores=np.array([0.9, 0.8], dtype=np.float32),
        )
        _io_match.write_pair_correspondences(tmp_path, "a.jpg", "b.jpg", pair)
        loaded = _io_match.load_pair_correspondences(tmp_path)
        assert set(loaded) == {("a.jpg", "b.jpg")}
        got = loaded[("a.jpg", "b.jpg")]
        assert got.mode == "indexed"
        assert got.indices is not None
        assert got.indices.dtype == np.int32  # integer dtype preserved
        np.testing.assert_array_equal(got.indices, pair.indices)
        assert got.scores is not None
        np.testing.assert_array_equal(got.scores, pair.scores)

    def test_coordinates_pair_round_trip(self, tmp_path: Path) -> None:
        pair = PairCorrespondences.from_coordinates(
            np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
            np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32),
        )
        _io_match.write_pair_correspondences(tmp_path, "a.jpg", "b.jpg", pair)
        got = _io_match.load_pair_correspondences(tmp_path)[("a.jpg", "b.jpg")]
        assert got.mode == "coordinates"
        assert got.coordinates_a is not None
        assert got.coordinates_b is not None
        np.testing.assert_array_equal(got.coordinates_a, pair.coordinates_a)
        np.testing.assert_array_equal(got.coordinates_b, pair.coordinates_b)

    def test_geometry_round_trip(self, tmp_path: Path) -> None:
        geometry = TwoViewGeometry(
            E=np.eye(3),
            F=np.full((3, 3), 2.0),
            H=np.arange(9, dtype=np.float64).reshape(3, 3),
            num_inliers=7,
        )
        pair = PairCorrespondences.from_indices(
            np.array([[0, 0]], dtype=np.int64), geometry=geometry
        )
        _io_match.write_pair_correspondences(tmp_path, "a.jpg", "b.jpg", pair, verified=True)
        # verified/ store is where a verified pass lands
        got = _io_match.load_pair_correspondences(tmp_path)[("a.jpg", "b.jpg")]
        assert got.geometry is not None
        assert got.geometry.num_inliers == 7
        np.testing.assert_array_equal(got.geometry.E, geometry.E)
        np.testing.assert_array_equal(got.geometry.F, geometry.F)
        np.testing.assert_array_equal(got.geometry.H, geometry.H)
        assert got.geometry.E.dtype == np.float64


# ---- CorrespondenceGraph reader -------------------------------------------


class TestCorrespondenceGraphReader:
    def _seed(self, store: Path) -> None:
        for name in ("a.jpg", "b.jpg"):
            _io_match.write_feature_set(
                store,
                name,
                FeatureSet(
                    keypoints=np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]], dtype=np.float32)
                ),
            )

    def test_empty_store_is_none(self, tmp_path: Path) -> None:
        assert _io_match.load_correspondence_graph(tmp_path / "nope") is None
        assert _io_match.load_correspondence_graph(None) is None

    def test_features_plus_matches_build_graph(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        _io_match.write_pair_correspondences(
            tmp_path, "a.jpg", "b.jpg", PairCorrespondences.from_indices(np.array([[0, 1], [1, 2]]))
        )
        graph = _io_match.load_correspondence_graph(tmp_path)
        assert graph is not None
        assert set(graph.features) == {"a.jpg", "b.jpg"}
        assert set(graph.pairs) == {("a.jpg", "b.jpg")}
        assert graph.pairs[("a.jpg", "b.jpg")].mode == "indexed"

    def test_verified_overrides_raw_matches(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        _io_match.write_pair_correspondences(
            tmp_path,
            "a.jpg",
            "b.jpg",
            PairCorrespondences.from_indices(np.array([[0, 1], [1, 2], [2, 0]])),
        )
        _io_match.write_pair_correspondences(
            tmp_path,
            "a.jpg",
            "b.jpg",
            PairCorrespondences.from_indices(
                np.array([[0, 1]]), geometry=TwoViewGeometry(num_inliers=1)
            ),
            verified=True,
        )
        graph = _io_match.load_correspondence_graph(tmp_path)
        assert graph is not None
        pair = graph.pairs[("a.jpg", "b.jpg")]
        assert len(pair) == 1  # the verified subset, not the 3 raw matches
        assert pair.geometry is not None


# ---- store anchoring -------------------------------------------------------


def test_store_root_anchors_on_database_path(tmp_path: Path) -> None:
    db_path = tmp_path / "recon" / "database.db"
    assert _io_match.correspondence_store_root(db_path) == tmp_path / "recon" / "io_correspondence"


# ---- pair enumeration ------------------------------------------------------


class TestEnumeratePairs:
    def test_exhaustive(self) -> None:
        assert _io_match.enumerate_pairs("exhaustive", {}, ["c", "a", "b"]) == [
            ("a", "b"),
            ("a", "c"),
            ("b", "c"),
        ]

    def test_sequential_window(self) -> None:
        pairs = _io_match.enumerate_pairs("sequential", {"overlap": 1}, ["a", "b", "c"])
        assert pairs == [("a", "b"), ("b", "c")]

    def test_explicit_from_spec(self) -> None:
        pairs = _io_match.enumerate_pairs(
            "explicit",
            {"image_pairs": [{"image_name1": "a", "image_name2": "c"}]},
            ["a", "b", "c"],
        )
        assert pairs == [("a", "c")]


# ---- fake io conformers ----------------------------------------------------


class _FakeMatcherBackend(StubBackend):
    """One backend covering the io FeatureExtractor / PairMatcher (detector-
    based) / GeometricVerifier Protocols with real numpy arrays."""

    def traits(self) -> MatcherTraits:  # type: ignore[override]
        return MatcherTraits(persistent_keypoints=True, detector_free=False)

    def extract(self, image: Any, *, options: Any = None) -> FeatureSet:
        # Four deterministic keypoints with float32 descriptors per image.
        keypoints = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], dtype=np.float32)
        descriptors = np.arange(4 * 2, dtype=np.float32).reshape(4, 2)
        return FeatureSet(keypoints=keypoints, descriptors=descriptors)

    def match_pair(self, a: Any, b: Any, *, options: Any = None) -> PairCorrespondences:
        # trivial i<->i matches over the first three keypoints
        return PairCorrespondences.from_indices(np.array([[0, 0], [1, 1], [2, 2]], dtype=np.int64))

    def verify(self, pair: Any, *, options: Any = None) -> PairCorrespondences:
        # keep the first two, attach an identity essential matrix
        return PairCorrespondences.from_indices(
            pair.indices[:2], geometry=TwoViewGeometry(E=np.eye(3), num_inliers=2)
        )


class _ClassicalMapperBackend(StubBackend):
    """A classical io Mapper (requires_correspondences=True) that proves the
    bridge fed it the graph the earlier stages produced."""

    seen: ClassVar[dict[str, Any]] = {}

    def traits(self) -> MapperTraits:  # type: ignore[override]
        return MapperTraits(
            requires_correspondences=True,
            accepts_pose_priors=False,
            accepts_depth_priors=False,
            accepts_calibration=False,
            emits_dense=False,
            metric_capable=False,
        )

    def map(self, views: Any, *, correspondences: Any = None, options: Any = None) -> MappingResult:
        assert correspondences is not None, "the io bridge must supply the correspondence graph"
        type(self).seen = {
            "features": sorted(correspondences.features),
            "pairs": sorted(correspondences.pairs),
        }
        poses = [SE3.identity()] + [None] * (len(views) - 1)
        return MappingResult(poses=tuple(poses), frame=FrameMeta())


def _use_backend(backend_cls: type) -> None:
    register_backend("stub", backend_cls)
    reset_capabilities_cache()


def _local_images(tmp_path: Path, names: list[str]) -> dict[str, Any]:
    img_dir = tmp_path / "imgs"
    img_dir.mkdir(exist_ok=True)
    for name in names:
        (img_dir / name).write_bytes(b"\xff\xd8\xff\xe0" + name.encode())
    return {"kind": "local", "image_list": names, "image_root": str(img_dir)}


def _task(kind: str, inputs: dict[str, Any], spec: dict[str, Any]) -> Task:
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


# ---- downstream-compat -----------------------------------------------------


def test_io_extract_output_feeds_v0_match_at_fixture_level(tmp_path: Path) -> None:
    """An io feature stage's output threads into a v0 match stage unchanged."""
    from sceneapi.server.workers.tasks import match as match_task

    db_path = tmp_path / "database.db"
    out = _io_match.run_io_extract(
        _FakeMatcherBackend(),
        backend=_FakeMatcherBackend(),
        db_path=db_path,
        image_root=tmp_path,
        image_list=["a.jpg", "b.jpg"],
        spec={"type": "sift"},
    )
    # v0-shaped output envelope
    assert out["database_path"] == str(db_path)
    assert out["num_images"] == 2
    artifact = out["artifacts"][0]
    assert artifact["kind"] == "features.database.stub"
    assert artifact["artifact_format"] == "stub.features.io.v1"
    assert artifact["schema_version"] == 1

    # the io-extract database_path threads into a v0 match task at fixture level
    v0_calls: list[str] = []

    class V0Matcher(StubBackend):
        def match(self, *, database_path: Path, mode: str, options: dict) -> dict:
            v0_calls.append(str(database_path))
            return {"num_matched_pairs": 0}

    _use_backend(V0Matcher)
    task = _task(
        "match",
        {"recon_id": "r1", "dataset_id": "d1", "database_path": out["database_path"]},
        {"pairs": {"strategy": "exhaustive"}, "matcher": {"type": "nn-mutual"}},
    )
    result = match_task.run(task)
    assert v0_calls == [out["database_path"]]
    assert result["strategy"] == "exhaustive"


# ---- e2e-lite: features -> matches -> verify -> map -----------------------


def test_io_pipeline_features_to_matches_to_verify_to_map(tmp_path: Path) -> None:
    """Drive the whole io chain through the task handlers with sealed output."""
    from sceneapi.server.workers.tasks import extract as extract_task
    from sceneapi.server.workers.tasks import map as map_task
    from sceneapi.server.workers.tasks import match as match_task
    from sceneapi.server.workers.tasks import verify as verify_task

    names = ["a.jpg", "b.jpg", "c.jpg"]
    materialization = _local_images(tmp_path, names)
    db_path = tmp_path / "database.db"
    common = {
        "project_id": "p1",
        "recon_id": "r1",
        "dataset_id": "d1",
        "database_path": str(db_path),
        "materialization": materialization,
    }

    _use_backend(_FakeMatcherBackend)

    # 1. extract -> FeatureSets in the io store
    extract_out = extract_task.run(_task("extract", dict(common), {"type": "sift"}))
    assert extract_out["num_images"] == 3
    store = _io_match.correspondence_store_root(db_path)
    assert sorted(_io_match.load_feature_sets(store)) == names

    # 2. match -> indexed PairCorrespondences for every exhaustive pair
    match_out = match_task.run(
        _task(
            "match",
            {"recon_id": "r1", "dataset_id": "d1", "database_path": str(db_path)},
            {"pairs": {"strategy": "exhaustive"}, "matcher": {"type": "nn-mutual"}},
        )
    )
    assert match_out["num_matched_pairs"] == 3  # C(3, 2)
    assert set(_io_match.load_pair_correspondences(store)) == {
        ("a.jpg", "b.jpg"),
        ("a.jpg", "c.jpg"),
        ("b.jpg", "c.jpg"),
    }

    # 3. verify -> the geometrically-consistent subset (+ geometry)
    verify_out = verify_task.run(
        _task("verify", {"recon_id": "r1", "dataset_id": "d1", "database_path": str(db_path)}, {})
    )
    assert verify_out["num_verified_pairs"] == 3
    graph = _io_match.load_correspondence_graph(store)
    assert graph is not None
    assert all(pair.geometry is not None for pair in graph.pairs.values())

    # 4. map -> a classical io Mapper consumes the bridged graph; snapshot sealed
    _ClassicalMapperBackend.seen = {}
    _use_backend(_ClassicalMapperBackend)
    map_out = map_task.run(_task("map", dict(common), {"kind": "incremental", "seed": 0}))

    assert _ClassicalMapperBackend.seen["features"] == names
    assert _ClassicalMapperBackend.seen["pairs"] == [
        ("a.jpg", "b.jpg"),
        ("a.jpg", "c.jpg"),
        ("b.jpg", "c.jpg"),
    ]
    assert map_out["snapshot_seq"] == 1
    sealed = Path(map_out["snapshot_path"])
    assert (sealed / "images.json").exists()
    (summary,) = map_out["models"]
    assert summary["num_reg_images"] == 1
    assert summary["num_unregistered_images"] == 2
