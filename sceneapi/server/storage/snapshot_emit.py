"""Convert an in-memory Reconstruction into a sealed-snapshot directory.

This is the producer side of the wire contract that
``sceneapi/server/api/v1/reconstructions.py`` advertises. The emitter writes:

  ``cameras.json``                 — :class:`CamerasFile`
  ``images.json``                  — :class:`ImagesFile` (poses + keypoints)
  ``rigs.json``                    — :class:`RigsFile` (if present)
  ``frames.json``                  — :class:`FramesFile` (if present)
  ``points.bin``                   — fixed-stride point3D records
  ``points_preview.bin``           — decimated subset of ``points.bin``
  ``observations_by_image.json``   — image_id → [point3d_ids]
  ``observations_by_point.json``   — point3d_id → [image_ids]

Pycolmap is **not** imported here. The function takes a duck-typed
``reconstruction`` so worker code can pass a real
``pycolmap.Reconstruction`` and tests can pass a synthetic stub. The
duck-typed surface required is documented inline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sceneapi.server.schemas.api.scene import (
    Camera,
    CamerasFile,
    Frame,
    FramesFile,
    ImagePose,
    ImagesFile,
    Point2D,
    Rig,
    Rigid3,
    RigsFile,
    Rotation,
)
from sceneapi.server.schemas.points_binary import Point3DRecord, encode_all
from sceneapi.server.storage import observations as obs_store
from sceneapi.server.storage._atomic import write_bytes as _atomic_write_bytes
from sceneapi.server.storage._atomic import write_text as _atomic_write_text

# ---- duck-typed converters ------------------------------------------------


def _quat_xyzw_to_rotation(quat_xyzw: Any) -> Rotation:
    """Pycolmap's ``Rotation3d.quat`` is Eigen-style ``(x, y, z, w)``;
    our wire is ``(w, x, y, z)``. Reorder."""
    x, y, z, w = float(quat_xyzw[0]), float(quat_xyzw[1]), float(quat_xyzw[2]), float(quat_xyzw[3])
    return Rotation(w=w, x=x, y=y, z=z)


def _rigid3_from_pycolmap(rigid: Any) -> Rigid3:
    """``rigid`` exposes ``rotation.quat`` (xyzw) and ``translation`` (3-vec)."""
    rot = _quat_xyzw_to_rotation(rigid.rotation.quat)
    t = rigid.translation
    return Rigid3(rotation=rot, translation=(float(t[0]), float(t[1]), float(t[2])))


def _camera_to_schema(cam: Any) -> Camera:
    model = getattr(cam, "model_name", None) or str(getattr(cam, "model", "UNKNOWN"))
    return Camera(
        camera_id=int(cam.camera_id),
        model=str(model),
        width=int(cam.width),
        height=int(cam.height),
        params=[float(p) for p in list(cam.params)],
        has_prior_focal_length=bool(getattr(cam, "has_prior_focal_length", False)),
    )


def _image_to_schema(img: Any) -> ImagePose:
    pts = []
    for p in getattr(img, "points2D", []) or []:
        xy = p.xy if hasattr(p, "xy") else (p.x, p.y)
        pid = getattr(p, "point3D_id", None)
        # pycolmap uses kInvalidPoint3DId = max uint64; treat that as None.
        pid_val: int | None = None
        if pid is not None and int(pid) < (1 << 63):
            pid_val = int(pid)
        pts.append(Point2D(xy=(float(xy[0]), float(xy[1])), point3d_id=pid_val))
    return ImagePose(
        image_id=int(img.image_id),
        name=str(img.name),
        camera_id=int(img.camera_id),
        cam_from_world=_rigid3_from_pycolmap(img.cam_from_world),
        points2D=pts,
    )


def _rig_to_schema(rig: Any) -> Rig:
    sensors: dict[str, Rigid3] = {}
    for sid, transform in (getattr(rig, "sensor_from_rig", None) or {}).items():
        sensors[str(sid)] = _rigid3_from_pycolmap(transform)
    return Rig(
        rig_id=int(rig.rig_id),
        ref_sensor_id=int(getattr(rig, "ref_sensor_id", 0)),
        sensor_from_rig=sensors,
    )


def _frame_to_schema(frame: Any) -> Frame:
    data_ids: dict[str, int] = {}
    for sid, did in (getattr(frame, "data_ids", None) or {}).items():
        data_ids[str(sid)] = int(did)
    return Frame(
        frame_id=int(frame.frame_id),
        rig_id=int(getattr(frame, "rig_id", 0)),
        rig_from_world=_rigid3_from_pycolmap(frame.rig_from_world),
        data_ids=data_ids,
    )


# ---- emitter --------------------------------------------------------------


def _bbox(
    records: list[Point3DRecord],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if not records:
        z = (0.0, 0.0, 0.0)
        return z, z
    xs = [r.xyz[0] for r in records]
    ys = [r.xyz[1] for r in records]
    zs = [r.xyz[2] for r in records]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def emit_snapshot_files(reconstruction: Any, out_dir: Path, *, preview_max: int = 10000) -> dict:
    """Write all advertised snapshot files for one Reconstruction into
    ``out_dir``. Returns a manifest of written paths and counts.

    ``reconstruction`` is duck-typed against ``pycolmap.Reconstruction``
    for testability — tests pass a small dataclass-based stub with the
    same attribute shape (see ``tests/unit/test_snapshot_emit.py``).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"out_dir": str(out_dir)}

    cameras = list((getattr(reconstruction, "cameras", None) or {}).values())
    images = list((getattr(reconstruction, "images", None) or {}).values())
    points3D = list((getattr(reconstruction, "points3D", None) or {}).values())
    rigs = list((getattr(reconstruction, "rigs", None) or {}).values())
    frames = list((getattr(reconstruction, "frames", None) or {}).values())

    cameras_file = CamerasFile(cameras=[_camera_to_schema(c) for c in cameras])
    _atomic_write_text(
        out_dir / "cameras.json",
        cameras_file.model_dump_json(by_alias=True, indent=2),
    )
    manifest["cameras"] = len(cameras)

    image_schemas = [_image_to_schema(i) for i in images]
    images_file = ImagesFile(images=image_schemas)
    _atomic_write_text(
        out_dir / "images.json",
        images_file.model_dump_json(by_alias=True, indent=2),
    )
    manifest["images"] = len(image_schemas)

    if rigs:
        rigs_file = RigsFile(rigs=[_rig_to_schema(r) for r in rigs])
        _atomic_write_text(
            out_dir / "rigs.json",
            rigs_file.model_dump_json(by_alias=True, indent=2),
        )
        manifest["rigs"] = len(rigs)
    if frames:
        frames_file = FramesFile(frames=[_frame_to_schema(f) for f in frames])
        _atomic_write_text(
            out_dir / "frames.json",
            frames_file.model_dump_json(by_alias=True, indent=2),
        )
        manifest["frames"] = len(frames)

    records: list[Point3DRecord] = []
    for p in points3D:
        pid = int(getattr(p, "point3D_id", 0) or 0)
        xyz = p.xyz
        color = getattr(p, "color", (0, 0, 0)) or (0, 0, 0)
        track = getattr(p, "track", None)
        track_len = len(getattr(track, "elements", []) or []) if track is not None else 0
        records.append(
            Point3DRecord(
                point3d_id=pid,
                xyz=(float(xyz[0]), float(xyz[1]), float(xyz[2])),
                rgb=(int(color[0]) & 0xFF, int(color[1]) & 0xFF, int(color[2]) & 0xFF),
                track_len=track_len,
            )
        )
    records.sort(key=lambda r: r.point3d_id)
    bbox_min, bbox_max = _bbox(records)
    _atomic_write_bytes(
        out_dir / "points.bin",
        encode_all(records, bbox_min=bbox_min, bbox_max=bbox_max),
    )
    manifest["points3D"] = len(records)

    if records and preview_max > 0 and len(records) > preview_max:
        stride = max(1, len(records) // preview_max)
        preview = records[::stride][:preview_max]
    else:
        preview = list(records)
    _atomic_write_bytes(
        out_dir / "points_preview.bin",
        encode_all(preview, bbox_min=bbox_min, bbox_max=bbox_max),
    )
    manifest["points_preview"] = len(preview)

    by_image: dict[int, list[obs_store.ImageObservationRow]] = {}
    by_point: dict[int, list[obs_store.PointObservationRow]] = {}
    for img in image_schemas:
        per_img: list[obs_store.ImageObservationRow] = []
        for kp_idx, pt in enumerate(img.points2D):
            if pt.point3d_id is None:
                continue
            per_img.append(
                obs_store.ImageObservationRow(
                    point3d_id=pt.point3d_id, x=pt.xy[0], y=pt.xy[1], kp_idx=kp_idx
                )
            )
            by_point.setdefault(pt.point3d_id, []).append(
                obs_store.PointObservationRow(
                    image_id=img.image_id, x=pt.xy[0], y=pt.xy[1], kp_idx=kp_idx
                )
            )
        if per_img:
            by_image[img.image_id] = per_img
    obs_store.write_observations_by_image(out_dir, by_image=by_image)
    obs_store.write_observations_by_point(out_dir, by_point=by_point)
    manifest["observations_by_image"] = len(by_image)
    manifest["observations_by_point"] = len(by_point)

    summary_path = out_dir / "summary.json"
    if not summary_path.exists():
        _atomic_write_text(
            summary_path,
            json.dumps(
                {
                    "num_reg_images": getattr(
                        reconstruction, "num_reg_images", lambda: len(images)
                    )()
                    if callable(getattr(reconstruction, "num_reg_images", None))
                    else len(images),
                    "num_points3D": len(records),
                },
                sort_keys=True,
                indent=2,
            ),
        )
    return manifest


__all__ = ["emit_snapshot_files"]
