"""The sceneio mapping path of the map worker task.

Bridges the task's materialized inputs into the neutral sceneio
``Mapper`` contract and the ``MappingResult`` back into the EXISTING
snapshot emission shape:

* ``ViewInput``s are built from the materialized image set **by path**
  (``MaterializedImage`` references — pixels are never loaded into
  memory for path-based flows); pose priors from the task inputs are
  attached when the mapper's traits accept them.
* A ``CorrespondenceGraph`` is supplied only when sealed feature/match
  artifacts can honestly be bridged AND the mapper's traits want them.
  ``requires_correspondences=True`` with no bridgeable artifacts is an
  honest 501 (``CapabilityUnavailableError``) — the engine-artifact
  bridge lands with the Step-6 adapters.
* Registered poses + the sparse ``TrackedPointCloud`` flow through the
  existing ``emit_snapshot_files`` writer (the same files the sealed
  snapshot serves); unregistered views are recorded in the submodel
  summary, never silently dropped.
* Dense per-view outputs (Pointmap + ConfidenceMap) are written as
  job-dir files referenced from the summary stats only — NO new wire
  or artifact format ids (dense wire exposure is Phase-C territory).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sceneio.data import SE3, Calibration, PosePrior, ViewInput
from sceneio.imagesource import MaterializedImage
from sceneio.mapping import Mapper, MappingOptions, MappingResult

from sceneapi.server.core.errors import CapabilityUnavailableError
from sceneapi.server.core.logging import get_logger
from sceneapi.server.storage.snapshot_emit import emit_snapshot_files
from sceneapi.server.workers._io_match import (
    correspondence_store_root,
    load_correspondence_graph,
)

_log = get_logger("sceneapi.workers.io_map")


# ---- task inputs -> ViewInput ---------------------------------------------


def pose_prior_from_wire(prior: dict[str, Any]) -> PosePrior | None:
    """Convert a stored per-image PosePrior dict (the wire schema shape:
    ``cam_from_world`` with a wxyz rotation + translation, optional
    36-float row-major covariance) into the sceneio ``PosePrior``.

    Returns None when the dict is not convertible — an unusable prior
    degrades to "no prior", it never fails the mapping task.
    """
    try:
        cam_from_world = prior.get("cam_from_world") or {}
        rotation = cam_from_world.get("rotation") or {}
        qvec = [rotation["w"], rotation["x"], rotation["y"], rotation["z"]]
        tvec = cam_from_world.get("translation")
        pose = SE3.from_colmap_world2cam(qvec, tvec)
        covariance = None
        raw_cov = prior.get("covariance")
        if raw_cov is not None:
            covariance = np.asarray(raw_cov, dtype=np.float64).reshape(6, 6)
        return PosePrior(pose=pose, covariance=covariance)
    except Exception as exc:
        _log.warning("io_map.pose_prior_unconvertible", error=str(exc))
        return None


def build_view_inputs(
    image_root: Path,
    image_list: list[str],
    *,
    pose_priors: dict[str, dict[str, Any]] | None = None,
    accepts_pose_priors: bool = False,
) -> list[ViewInput]:
    """Path-referencing ViewInputs for the materialized image set."""
    priors = pose_priors or {}
    views: list[ViewInput] = []
    for name in image_list:
        pose_prior = None
        if accepts_pose_priors and name in priors:
            pose_prior = pose_prior_from_wire(priors[name])
        views.append(
            ViewInput(
                image=MaterializedImage(name=name, abs_path=image_root / name),
                name=name,
                pose_prior=pose_prior,
            )
        )
    return views


# ---- MappingResult -> snapshot emission shape -----------------------------


@dataclass(frozen=True)
class _IoRotation:
    quat: tuple[float, float, float, float]  # (x, y, z, w) — pycolmap order


@dataclass(frozen=True)
class _IoRigid3:
    rotation: _IoRotation
    translation: tuple[float, float, float]


@dataclass(frozen=True)
class _IoCamera:
    camera_id: int
    model_name: str
    width: int
    height: int
    params: tuple[float, ...]
    has_prior_focal_length: bool = False


@dataclass(frozen=True)
class _IoImage:
    image_id: int
    name: str
    camera_id: int
    cam_from_world: _IoRigid3
    points2D: tuple = ()


@dataclass(frozen=True)
class _IoTrack:
    elements: tuple = ()


@dataclass(frozen=True)
class _IoPoint3D:
    point3D_id: int
    xyz: tuple[float, float, float]
    color: tuple[int, int, int]
    track: _IoTrack | None = None


@dataclass
class _IoReconstruction:
    """Duck-typed twin of ``pycolmap.Reconstruction`` for the emitter."""

    cameras: dict[int, _IoCamera] = field(default_factory=dict)
    images: dict[int, _IoImage] = field(default_factory=dict)
    points3D: dict[int, _IoPoint3D] = field(default_factory=dict)

    def num_reg_images(self) -> int:
        return len(self.images)


def _rigid_from_se3(pose: SE3) -> _IoRigid3:
    qvec_wxyz, tvec = pose.to_colmap_world2cam()
    w, x, y, z = (float(v) for v in qvec_wxyz)
    return _IoRigid3(
        rotation=_IoRotation(quat=(x, y, z, w)),
        translation=(float(tvec[0]), float(tvec[1]), float(tvec[2])),
    )


def _camera_from_calibration(camera_id: int, calibration: Calibration) -> _IoCamera | None:
    if calibration.intrinsics is None:
        return None  # ray-map calibrations have no COLMAP camera row
    intrinsics = calibration.intrinsics
    return _IoCamera(
        camera_id=camera_id,
        model_name=intrinsics.model.value,
        width=int(intrinsics.width),
        height=int(intrinsics.height),
        params=tuple(float(p) for p in np.asarray(intrinsics.params).reshape(-1)),
    )


def reconstruction_from_result(result: MappingResult, names: list[str]) -> _IoReconstruction:
    """Registered poses + the tracked cloud as an emitter-compatible object.

    Image ids are 1-based view indices (unregistered views get NO image
    row — that is what "unregistered" means in a COLMAP-shaped model);
    each registered view gets its own camera row when the result carries
    a parametric calibration for it.
    """
    rec = _IoReconstruction()
    for index, pose in enumerate(result.poses):
        if pose is None:
            continue
        image_id = index + 1
        camera_id = index + 1
        if result.calibrations is not None and result.calibrations[index] is not None:
            camera = _camera_from_calibration(camera_id, result.calibrations[index])
            if camera is not None:
                rec.cameras[camera_id] = camera
        name = names[index] if index < len(names) else f"view_{index:05d}"
        rec.images[image_id] = _IoImage(
            image_id=image_id,
            name=name,
            camera_id=camera_id,
            cam_from_world=_rigid_from_se3(pose),
        )
    geometry = result.geometry
    if geometry is not None:
        xyz = np.asarray(geometry.xyz, dtype=np.float64)
        rgb = None if geometry.rgb is None else np.asarray(geometry.rgb)
        for point_index in range(len(geometry)):
            track = None
            if geometry.tracks is not None:
                track = _IoTrack(elements=tuple(geometry.tracks[point_index]))
            color = (0, 0, 0) if rgb is None else tuple(int(c) for c in rgb[point_index])
            rec.points3D[point_index + 1] = _IoPoint3D(
                point3D_id=point_index + 1,
                xyz=tuple(float(v) for v in xyz[point_index]),
                color=color,  # type: ignore[arg-type]
                track=track,
            )
    return rec


def write_dense_outputs(result: MappingResult, job_dir: Path) -> list[str]:
    """Write per-view dense payloads as job-dir files; return their paths.

    Deliberately NOT registered as artifacts and NOT given a format id:
    dense wire exposure is Phase-C territory. The paths are referenced
    from the submodel summary stats only.
    """
    if result.dense is None:
        return []
    dense_dir = job_dir / "dense"
    dense_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for index, entry in enumerate(result.dense):
        if entry is None:
            continue
        pointmap, confidence = entry
        out_path = dense_dir / f"view_{index:05d}.npz"
        np.savez_compressed(
            out_path,
            points=pointmap.points,
            frame=np.array(pointmap.frame),
            confidence=confidence.values,
        )
        written.append(str(out_path))
    return written


def _json_safe_stats(stats: dict[str, Any]) -> dict[str, Any]:
    try:
        json.dumps(stats)
    except (TypeError, ValueError):
        return {key: repr(value) for key, value in stats.items()}
    return stats


def run_io_mapping(
    mapper: Mapper,
    *,
    kind: str,
    image_root: Path,
    image_list: list[str],
    sparse_root: Path,
    job_dir: Path,
    spec: dict[str, Any],
    pose_priors: dict[str, dict[str, Any]] | None = None,
    input_artifacts: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Run the sceneio mapping path; emit snapshot files; return the
    submodel summary (the ``models`` entry of the task result).

    ``db_path`` anchors the io correspondence store (the same
    ``database_path`` the extract/match/verify stages wrote it beside);
    when a ``requires_correspondences=True`` mapper is registered its
    :class:`~sceneio.data.CorrespondenceGraph` is read back from that
    store. ``input_artifacts`` is retained for symmetry with the v0 map
    task inputs.
    """
    _ = input_artifacts
    traits = mapper.traits()
    views = build_view_inputs(
        image_root,
        image_list,
        pose_priors=pose_priors,
        accepts_pose_priors=traits.accepts_pose_priors,
    )
    correspondences = None
    if traits.requires_correspondences:
        store_root = correspondence_store_root(db_path) if db_path is not None else None
        correspondences = load_correspondence_graph(store_root)
        if correspondences is None:
            raise CapabilityUnavailableError(
                capability=f"map.{kind}",
                reason=(
                    "the registered sceneio Mapper requires a correspondence "
                    "graph (traits.requires_correspondences=True) and no sealed "
                    "feature/match artifacts could be bridged into one"
                ),
            )
    raw_seed = spec.get("seed")
    raw_max_views = spec.get("max_views")
    # The portable ``max_init_points`` cap (FeedForwardSpec) rides into the
    # neutral options bag under the key dense fusing mappers read —
    # ``extra["max_points"]`` (e.g. the MapAnything provider, default 200k).
    # It takes precedence over any ``backend_options["max_points"]``; when
    # unset the key is absent and the provider's own default applies.
    # Mappers that don't fuse (the classical StubBackend path) ignore it.
    extra = dict(spec.get("backend_options") or {})
    raw_max_init_points = spec.get("max_init_points")
    if (
        isinstance(raw_max_init_points, int)
        and not isinstance(raw_max_init_points, bool)
        and raw_max_init_points >= 1
    ):
        extra["max_points"] = raw_max_init_points
    options = MappingOptions(
        max_views=raw_max_views if isinstance(raw_max_views, int) and raw_max_views >= 1 else None,
        seed=raw_seed if isinstance(raw_seed, int) and not isinstance(raw_seed, bool) else None,
        extra=extra,
    )
    result = mapper.map(views, correspondences=correspondences, options=options)

    rec = reconstruction_from_result(result, image_list)
    emit_snapshot_files(rec, sparse_root)
    dense_paths = write_dense_outputs(result, job_dir)

    mask = result.registered_mask
    unregistered = [
        views[index].name or f"view_{index:05d}"
        for index in range(len(views))
        if not bool(mask[index])
    ]
    summary: dict[str, Any] = {
        "idx": 0,
        "num_images": len(views),
        "num_reg_images": int(mask.sum()),
        "num_unregistered_images": len(unregistered),
        "unregistered_images": unregistered,
        "num_points3D": len(rec.points3D),
        "frame": {
            "world_frame": result.frame.world_frame,
            "scale": result.frame.scale,
            "scale_provenance": result.frame.scale_provenance,
        },
        "stats": _json_safe_stats(dict(result.stats)),
    }
    if dense_paths:
        summary["dense_outputs"] = dense_paths
    return summary


__all__ = [
    "build_view_inputs",
    "load_correspondence_graph",
    "pose_prior_from_wire",
    "reconstruction_from_result",
    "run_io_mapping",
    "write_dense_outputs",
]
