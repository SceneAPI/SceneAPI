"""The sceneapi-io map path of the map worker (P8 Step 5).

Covers the input bridge (ViewInputs by path, pose-prior conversion),
the result bridge (MappingResult -> snapshot emission shape, with
unregistered views persisted in the summary, never silently dropped),
dense job-dir persistence, the honest 501 for correspondence-requiring
mappers without bridgeable artifacts, and the dual-dispatch preference
(io Mapper wins over v0 run_mapping).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sceneapi_io.data import (
    SE3,
    Calibration,
    CameraIntrinsics,
    CameraModel,
    ConfidenceMap,
    FrameMeta,
    Pointmap,
    TrackedPointCloud,
)
from sceneapi_io.mapping import MapperTraits, MappingResult

from sceneapi.server.adapters.registry import register_backend
from sceneapi.server.adapters.stub_backend import StubBackend
from sceneapi.server.core.capabilities import reset_capabilities_cache
from sceneapi.server.core.errors import CapabilityUnavailableError
from sceneapi.server.core.ids import new_id
from sceneapi.server.db.models import Task
from sceneapi.server.workers import _io_map
from sceneapi.server.workers.tasks import map as map_task

pytestmark = pytest.mark.unit


WIRE_PRIOR: dict[str, Any] = {
    "cam_from_world": {
        "rotation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
        "translation": (1.0, 2.0, 3.0),
    },
    "covariance": [float(i == j) for i in range(6) for j in range(6)],
}


class TestPosePriorFromWire:
    def test_valid_prior_converts(self) -> None:
        prior = _io_map.pose_prior_from_wire(WIRE_PRIOR)
        assert prior is not None
        world2cam = prior.pose.as_convention("opencv_world2cam")
        np.testing.assert_allclose(world2cam.translation, [1.0, 2.0, 3.0])
        assert prior.covariance is not None
        np.testing.assert_allclose(prior.covariance, np.eye(6))

    def test_unconvertible_prior_degrades_to_none(self) -> None:
        assert _io_map.pose_prior_from_wire({"cam_from_world": {"rotation": {}}}) is None
        assert _io_map.pose_prior_from_wire({}) is None


class TestBuildViewInputs:
    def test_paths_referenced_not_loaded(self, tmp_path: Path) -> None:
        views = _io_map.build_view_inputs(tmp_path, ["a.jpg", "b.jpg"])
        assert [view.name for view in views] == ["a.jpg", "b.jpg"]
        # MaterializedImage references — no pixel loading for path flows
        assert all(view.image.abs_path == tmp_path / view.name for view in views)
        assert all(view.pose_prior is None for view in views)

    def test_priors_attached_only_when_traits_accept(self, tmp_path: Path) -> None:
        priors = {"a.jpg": WIRE_PRIOR}
        accepted = _io_map.build_view_inputs(
            tmp_path, ["a.jpg", "b.jpg"], pose_priors=priors, accepts_pose_priors=True
        )
        assert accepted[0].pose_prior is not None
        assert accepted[1].pose_prior is None
        ignored = _io_map.build_view_inputs(
            tmp_path, ["a.jpg"], pose_priors=priors, accepts_pose_priors=False
        )
        assert ignored[0].pose_prior is None


def _result_with_unregistered() -> MappingResult:
    calibration = Calibration.from_intrinsics(
        CameraIntrinsics(
            model=CameraModel.SIMPLE_PINHOLE,
            width=4,
            height=4,
            params=np.array([4.0, 2.0, 2.0]),
        )
    )
    return MappingResult(
        poses=(SE3.identity(), None, SE3.identity()),
        frame=FrameMeta(),
        calibrations=(calibration, None, None),
        geometry=TrackedPointCloud(
            xyz=np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 1.0]], dtype=np.float32),
            rgb=np.array([[255, 0, 0], [0, 255, 0]], dtype=np.uint8),
        ),
        dense=(
            (
                Pointmap(points=np.zeros((2, 2, 3), dtype=np.float32)),
                ConfidenceMap(values=np.full((2, 2), 0.25, dtype=np.float32)),
            ),
            None,
            None,
        ),
        stats={"solver": "test"},
    )


class TestReconstructionFromResult:
    def test_unregistered_views_get_no_image_rows(self) -> None:
        rec = _io_map.reconstruction_from_result(
            _result_with_unregistered(), ["a.jpg", "b.jpg", "c.jpg"]
        )
        assert sorted(img.name for img in rec.images.values()) == ["a.jpg", "c.jpg"]
        assert rec.num_reg_images() == 2
        # camera row only where a parametric calibration exists
        assert list(rec.cameras) == [1]
        assert rec.cameras[1].model_name == "SIMPLE_PINHOLE"
        assert len(rec.points3D) == 2
        assert rec.points3D[1].color == (255, 0, 0)

    def test_pose_direction_is_colmap_world2cam(self) -> None:
        cam2world = SE3(np.eye(3), np.array([1.0, 2.0, 3.0]))
        result = MappingResult(poses=(cam2world,), frame=FrameMeta())
        rec = _io_map.reconstruction_from_result(result, ["a.jpg"])
        translation = rec.images[1].cam_from_world.translation
        np.testing.assert_allclose(translation, [-1.0, -2.0, -3.0])


class TestWriteDenseOutputs:
    def test_only_present_entries_written(self, tmp_path: Path) -> None:
        written = _io_map.write_dense_outputs(_result_with_unregistered(), tmp_path)
        assert len(written) == 1
        payload = np.load(written[0])
        assert payload["points"].shape == (2, 2, 3)
        assert payload["confidence"].shape == (2, 2)
        assert str(payload["frame"]) == "world"

    def test_no_dense_writes_nothing(self, tmp_path: Path) -> None:
        result = MappingResult(poses=(SE3.identity(),), frame=FrameMeta())
        assert _io_map.write_dense_outputs(result, tmp_path) == []
        assert not (tmp_path / "dense").exists()


class _ClassicalIoMapper(StubBackend):
    """io Mapper whose traits demand correspondences."""

    def traits(self) -> MapperTraits:
        return MapperTraits(
            requires_correspondences=True,
            accepts_pose_priors=False,
            accepts_depth_priors=False,
            accepts_calibration=False,
            emits_dense=False,
            metric_capable=False,
        )


def test_requires_correspondences_without_artifacts_is_honest_501(tmp_path: Path) -> None:
    mapper = _ClassicalIoMapper()
    with pytest.raises(CapabilityUnavailableError) as excinfo:
        _io_map.run_io_mapping(
            mapper,
            kind="incremental",
            image_root=tmp_path,
            image_list=["a.jpg", "b.jpg"],
            sparse_root=tmp_path / "sparse",
            job_dir=tmp_path / "job",
            spec={"kind": "incremental"},
        )
    assert excinfo.value.extras.get("capability") == "map.incremental"


# ---- max_init_points cap -> MappingOptions.extra["max_points"] ------------


class _CapturingMapper(StubBackend):
    """Feed-forward stub that records the MappingOptions it is handed."""

    def __init__(self) -> None:
        self.captured: Any = None

    def map(self, views: Any, *, correspondences: Any = None, options: Any = None) -> Any:
        self.captured = options
        return super().map(views, correspondences=correspondences, options=options)


def _capture_options(tmp_path: Path, spec: dict[str, Any]) -> Any:
    mapper = _CapturingMapper()
    _io_map.run_io_mapping(
        mapper,
        kind="feed_forward",
        image_root=tmp_path,
        image_list=["a.jpg", "b.jpg"],
        sparse_root=tmp_path / "sparse",
        job_dir=tmp_path / "job",
        spec=spec,
    )
    return mapper.captured


def test_max_init_points_threads_into_options_extra_max_points(tmp_path: Path) -> None:
    options = _capture_options(tmp_path, {"kind": "feed_forward", "max_init_points": 12345})
    # The key dense-fusing mappers read (e.g. MapAnything: options.extra["max_points"]).
    assert options.extra["max_points"] == 12345


def test_max_init_points_unset_leaves_max_points_absent(tmp_path: Path) -> None:
    # Absent key -> the provider's own default (200k for MapAnything) applies.
    options = _capture_options(tmp_path, {"kind": "feed_forward"})
    assert "max_points" not in options.extra


def test_max_init_points_overrides_backend_options_max_points(tmp_path: Path) -> None:
    options = _capture_options(
        tmp_path,
        {
            "kind": "feed_forward",
            "max_init_points": 100,
            "backend_options": {"max_points": 999},
        },
    )
    assert options.extra["max_points"] == 100


# ---- map task handler: dual dispatch --------------------------------------


def _map_stage_task(tmp_path: Path, *, kind: str, names: list[str]) -> Task:
    img_dir = tmp_path / "imgs"
    img_dir.mkdir(exist_ok=True)
    for name in names:
        (img_dir / name).write_bytes(b"\xff\xd8\xff\xe0" + name.encode())
    inputs = {
        "project_id": "p1",
        "recon_id": "r1",
        "dataset_id": "d1",
        "database_path": str(tmp_path / "database.db"),
        "materialization": {"kind": "local", "image_list": names, "image_root": str(img_dir)},
    }
    return Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=new_id(),
        kind="map",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        task_state_json={"inputs": inputs, "spec": {"kind": kind, "seed": 0}},
    )


def _use_backend(backend_cls: type) -> None:
    register_backend("stub", backend_cls)
    reset_capabilities_cache()


def test_map_task_feed_forward_on_stub_persists_and_records_unregistered(
    tmp_path: Path,
) -> None:
    _use_backend(StubBackend)
    task = _map_stage_task(tmp_path, kind="feed_forward", names=["a.jpg", "b.jpg", "c.jpg"])
    out = map_task.run(task)

    assert out["snapshot_seq"] == 1
    (summary,) = out["models"]
    assert summary["num_images"] == 3
    assert summary["num_reg_images"] == 1
    assert summary["num_unregistered_images"] == 2
    assert sorted(summary["unregistered_images"]) == ["b.jpg", "c.jpg"]
    assert summary["num_points3D"] == 8
    assert summary["frame"]["scale"] == "arbitrary"
    assert summary["stats"]["stub"] is True

    sealed = Path(out["snapshot_path"])
    assert (sealed / "points.bin").stat().st_size > 44  # header + the stub's points
    assert (sealed / "images.json").exists()

    # dense payloads are job-dir files referenced from the summary only
    dense_paths = [Path(p) for p in summary["dense_outputs"]]
    assert len(dense_paths) == 1
    assert dense_paths[0].is_file()
    assert Path(out["job_dir"]) in dense_paths[0].parents
    # ... and never surface as artifacts (no new wire/artifact format ids)
    assert all("dense" not in a["kind"] for a in out["artifacts"])
    assert {a["kind"] for a in out["artifacts"]} == {
        "reconstruction.sparse.v1",
        "reconstruction.snapshot",
        "reconstruction.submodel",
    }


def test_map_task_prefers_io_mapper_over_v0_run_mapping(tmp_path: Path) -> None:
    calls: list[str] = []

    class DualMappingBackend(StubBackend):
        def run_mapping(self, **kwargs: Any) -> Any:
            calls.append("v0")
            raise AssertionError("v0 run_mapping must not be called when an io Mapper exists")

    _use_backend(DualMappingBackend)
    task = _map_stage_task(tmp_path, kind="incremental", names=["a.jpg", "b.jpg"])
    out = map_task.run(task)
    assert calls == []
    assert out["models"][0]["stats"]["stub"] is True


def test_map_task_feed_forward_without_io_mapper_is_honest_501(tmp_path: Path) -> None:
    class V0OnlyBackend:
        name = "stub"
        version = "0.0.1"
        vendor = "test"

        def capabilities(self) -> set[str]:
            return set()

        def runtime_versions(self) -> dict[str, str]:
            return {"stub_version": "0.0.1"}

        def run_mapping(self, **kwargs: Any) -> Any:
            raise AssertionError("run_mapping has no feed-forward form and must not be tried")

    _use_backend(V0OnlyBackend)
    task = _map_stage_task(tmp_path, kind="feed_forward", names=["a.jpg"])
    with pytest.raises(CapabilityUnavailableError) as excinfo:
        map_task.run(task)
    assert excinfo.value.extras.get("capability") == "map.feed_forward"


def test_map_task_requiring_mapper_without_artifacts_501s(tmp_path: Path) -> None:
    _use_backend(_ClassicalIoMapper)
    task = _map_stage_task(tmp_path, kind="incremental", names=["a.jpg", "b.jpg"])
    with pytest.raises(CapabilityUnavailableError) as excinfo:
        map_task.run(task)
    assert excinfo.value.extras.get("capability") == "map.incremental"


def test_map_task_v0_fallback_unchanged(tmp_path: Path) -> None:
    """A v0-only backend still runs through run_mapping untouched."""
    calls: list[dict[str, Any]] = []

    class V0Backend:
        name = "stub"
        version = "0.0.1"
        vendor = "test"

        def capabilities(self) -> set[str]:
            return set()

        def runtime_versions(self) -> dict[str, str]:
            return {"stub_version": "0.0.1"}

        def run_mapping(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return [{"idx": 0, "num_reg_images": 0}], []

    _use_backend(V0Backend)
    task = _map_stage_task(tmp_path, kind="incremental", names=["a.jpg"])
    out = map_task.run(task)
    assert len(calls) == 1
    assert calls[0]["kind"] == "incremental"
    assert out["models"] == [{"idx": 0, "num_reg_images": 0}]
