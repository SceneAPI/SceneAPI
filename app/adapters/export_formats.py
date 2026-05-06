"""Sparse-model export formats for modern downstream pipelines.

Each function reads a duck-typed reconstruction (the same shape the
snapshot emitter expects) and writes a directory of files appropriate
to the target tool. Pure Python — no pycolmap import here. Backend
implementations call these after loading the reconstruction with
their own engine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.storage._atomic import write_text as _atomic_write_text


def _quat_xyzw_to_wxyz(q: Any) -> list[float]:
    return [float(q[3]), float(q[0]), float(q[1]), float(q[2])]


def _build_transform_matrix_world_from_cam(rec_image: Any) -> list[list[float]]:
    """Compute the 4x4 ``world_from_cam`` matrix (NeRFStudio /
    instant-ngp convention) from a pycolmap-shaped image. Engines
    typically expose ``cam_from_world`` (Eigen rotation + translation);
    we invert.
    """
    import numpy as np

    cfw = rec_image.cam_from_world
    qx, qy, qz, qw = (
        float(cfw.rotation.quat[0]),
        float(cfw.rotation.quat[1]),
        float(cfw.rotation.quat[2]),
        float(cfw.rotation.quat[3]),
    )
    # Quaternion (xyzw) → 3x3 rotation matrix.
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    R = np.array(
        [
            [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
            [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
            [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
        ],
        dtype=np.float64,
    )
    t = np.array(cfw.translation, dtype=np.float64).reshape(3, 1)
    # Invert the rigid transform: world_from_cam = (cam_from_world)^-1
    R_wc = R.T
    t_wc = -R.T @ t
    M = np.eye(4)
    M[:3, :3] = R_wc
    M[:3, 3] = t_wc[:, 0]
    return M.tolist()


def _camera_intrinsics(cam: Any) -> dict:
    """Best-effort extraction of pinhole intrinsics from a COLMAP
    camera. Different camera models pack params differently — we
    handle the common ones; fall back to the SIMPLE_RADIAL convention
    (params[0]=f, params[1]=cx, params[2]=cy)."""
    model = getattr(cam, "model_name", None) or str(getattr(cam, "model", "UNKNOWN"))
    p = list(cam.params)
    out = {"camera_model": str(model), "w": int(cam.width), "h": int(cam.height)}
    if model in ("PINHOLE",):
        out.update({"fl_x": p[0], "fl_y": p[1], "cx": p[2], "cy": p[3]})
    elif model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
        out.update({"fl_x": p[0], "fl_y": p[0], "cx": p[1], "cy": p[2]})
        if len(p) >= 4 and model.startswith("SIMPLE_RADIAL"):
            out["k1"] = p[3]
        if model == "RADIAL" and len(p) >= 5:
            out["k1"] = p[3]
            out["k2"] = p[4]
    elif model == "OPENCV":
        out.update(
            {
                "fl_x": p[0],
                "fl_y": p[1],
                "cx": p[2],
                "cy": p[3],
                "k1": p[4],
                "k2": p[5],
                "p1": p[6],
                "p2": p[7],
            }
        )
    elif model == "OPENCV_FISHEYE":
        out.update(
            {
                "fl_x": p[0],
                "fl_y": p[1],
                "cx": p[2],
                "cy": p[3],
                "k1": p[4],
                "k2": p[5],
                "k3": p[6],
                "k4": p[7],
                "is_fisheye": True,
            }
        )
    else:
        out.update(
            {
                "fl_x": p[0] if p else 0.0,
                "fl_y": p[0] if p else 0.0,
                "cx": cam.width / 2,
                "cy": cam.height / 2,
            }
        )
    return out


# ---- NeRFStudio --------------------------------------------------------


def export_nerfstudio(reconstruction: Any, out_dir: Path) -> Path:
    """Write a NeRFStudio-shaped ``transforms.json`` under ``out_dir``.

    Camera intrinsics are pulled from the first camera (NeRFStudio's
    base format assumes shared intrinsics; per-frame intrinsics are a
    superset feature). Multiple cameras → use the first and warn via
    a sidecar ``warnings.json``."""
    cameras = list(getattr(reconstruction, "cameras", {}).values())
    images = list(getattr(reconstruction, "images", {}).values())
    if not cameras or not images:
        raise ValueError("nerfstudio export needs at least one camera + one image")

    primary_cam = cameras[0]
    intrinsics = _camera_intrinsics(primary_cam)

    frames = []
    for img in images:
        frames.append(
            {
                "file_path": f"./images/{img.name}",
                "transform_matrix": _build_transform_matrix_world_from_cam(img),
                "camera_id": int(img.camera_id),
            }
        )
    transforms = {
        **intrinsics,
        "frames": frames,
        # NeRFStudio convention: orientation_method/applied_transform omitted —
        # let the consumer auto-orient.
    }
    out = out_dir / "transforms.json"
    _atomic_write_text(out, json.dumps(transforms, indent=2, sort_keys=True))
    if len(cameras) > 1:
        _atomic_write_text(
            out_dir / "warnings.json",
            json.dumps(
                {
                    "multi_camera": (
                        f"reconstruction has {len(cameras)} cameras; "
                        f"transforms.json uses camera_id={primary_cam.camera_id}'s "
                        f"intrinsics. Per-frame intrinsics are not yet emitted."
                    )
                },
                indent=2,
            ),
        )
    return out


# ---- instant-ngp -------------------------------------------------------


def export_instant_ngp(reconstruction: Any, out_dir: Path) -> Path:
    """instant-ngp's ``transforms.json`` is nearly identical to
    NeRFStudio's but uses ``aabb_scale`` (scene scale hint) and per-
    frame fields. Re-uses the NeRFStudio writer + adds the extras."""
    out = export_nerfstudio(reconstruction, out_dir)
    body = json.loads(out.read_text(encoding="utf-8"))
    body["aabb_scale"] = 16
    body["scale"] = 1.0
    body["offset"] = [0.5, 0.5, 0.5]
    _atomic_write_text(out, json.dumps(body, indent=2, sort_keys=True))
    return out


# ---- Gaussian Splatting ------------------------------------------------


def export_gaussian_splatting(reconstruction: Any, out_dir: Path) -> Path:
    """Gaussian Splatting (3DGS) consumes COLMAP-format text dumps
    (cameras.txt, images.txt, points3D.txt) under a ``sparse/0/``
    subdirectory. We can't always assume the backend's
    ``rec.write_text`` is callable, so emit the COLMAP text shape from
    the duck-typed reconstruction directly."""
    sparse_dir = out_dir / "sparse" / "0"
    sparse_dir.mkdir(parents=True, exist_ok=True)
    cameras = list(getattr(reconstruction, "cameras", {}).values())
    images = list(getattr(reconstruction, "images", {}).values())
    points3D = list(getattr(reconstruction, "points3D", {}).values())

    cam_lines = ["# Camera list with one line of data per camera:"]
    for c in cameras:
        params = " ".join(str(float(p)) for p in c.params)
        cam_lines.append(
            f"{int(c.camera_id)} {getattr(c, 'model_name', 'PINHOLE')} "
            f"{int(c.width)} {int(c.height)} {params}"
        )
    _atomic_write_text(sparse_dir / "cameras.txt", "\n".join(cam_lines) + "\n")

    img_lines = ["# Image list with two lines of data per image:"]
    for img in images:
        cfw = img.cam_from_world
        q = list(cfw.rotation.quat)
        # COLMAP text format expects QW QX QY QZ:
        qwxyz = _quat_xyzw_to_wxyz(q)
        t = list(cfw.translation)
        img_lines.append(
            f"{int(img.image_id)} "
            f"{qwxyz[0]} {qwxyz[1]} {qwxyz[2]} {qwxyz[3]} "
            f"{float(t[0])} {float(t[1])} {float(t[2])} "
            f"{int(img.camera_id)} {img.name}"
        )
        # 2nd line per image: POINTS2D as X Y POINT3D_ID triples.
        pts_line: list[str] = []
        for p2d in getattr(img, "points2D", []) or []:
            xy = p2d.xy if hasattr(p2d, "xy") else (p2d.x, p2d.y)
            pid = getattr(p2d, "point3D_id", -1)
            pts_line.extend([str(float(xy[0])), str(float(xy[1])), str(int(pid))])
        img_lines.append(" ".join(pts_line))
    _atomic_write_text(sparse_dir / "images.txt", "\n".join(img_lines) + "\n")

    pt_lines = ["# 3D point list with one line of data per point:"]
    for p in points3D:
        pid = int(getattr(p, "point3D_id", 0) or 0)
        x, y, z = (float(p.xyz[i]) for i in range(3))
        color = getattr(p, "color", (200, 200, 200)) or (200, 200, 200)
        r, g, b = (int(color[i]) & 0xFF for i in range(3))
        err = float(getattr(p, "error", 0.0))
        track = getattr(p, "track", None)
        track_pairs: list[str] = []
        if track is not None:
            for el in getattr(track, "elements", []) or []:
                track_pairs.extend([str(int(el.image_id)), str(int(el.point2D_idx))])
        pt_lines.append(f"{pid} {x} {y} {z} {r} {g} {b} {err} " + " ".join(track_pairs))
    _atomic_write_text(sparse_dir / "points3D.txt", "\n".join(pt_lines) + "\n")
    return sparse_dir


# ---- Kapture ---------------------------------------------------------


def export_kapture(reconstruction: Any, out_dir: Path) -> Path:
    """Naver Labs' Kapture format: per-modality text files under
    ``sensors/`` and ``reconstruction/``. We emit the minimum
    Kapture sensors + trajectories + keypoints needed for visual-
    localization workflows."""
    sensors_dir = out_dir / "sensors"
    recon_dir = out_dir / "reconstruction"
    sensors_dir.mkdir(parents=True, exist_ok=True)
    recon_dir.mkdir(parents=True, exist_ok=True)

    cameras = list(getattr(reconstruction, "cameras", {}).values())
    images = list(getattr(reconstruction, "images", {}).values())

    # sensors.txt: id, name, type, model, model_params...
    sensor_lines = ["# kapture format: 1.1", "# sensor_id, name, sensor_type, sensor_params"]
    for c in cameras:
        params = ", ".join(str(float(p)) for p in c.params)
        sensor_lines.append(
            f"cam{int(c.camera_id)}, cam{int(c.camera_id)}, camera, "
            f"{getattr(c, 'model_name', 'PINHOLE')}, "
            f"{int(c.width)}, {int(c.height)}, {params}"
        )
    _atomic_write_text(sensors_dir / "sensors.txt", "\n".join(sensor_lines) + "\n")

    # records_camera.txt: timestamp, sensor_id, image_path
    records = ["# kapture format: 1.1", "# timestamp, sensor_id, image_path"]
    for idx, img in enumerate(images):
        records.append(f"{idx}, cam{int(img.camera_id)}, {img.name}")
    _atomic_write_text(sensors_dir / "records_camera.txt", "\n".join(records) + "\n")

    # trajectories.txt: timestamp, sensor_id, qw qx qy qz tx ty tz
    traj = ["# kapture format: 1.1", "# timestamp, sensor_id, qw, qx, qy, qz, tx, ty, tz"]
    for idx, img in enumerate(images):
        cfw = img.cam_from_world
        qwxyz = _quat_xyzw_to_wxyz(list(cfw.rotation.quat))
        t = list(cfw.translation)
        traj.append(
            f"{idx}, cam{int(img.camera_id)}, "
            f"{qwxyz[0]}, {qwxyz[1]}, {qwxyz[2]}, {qwxyz[3]}, "
            f"{float(t[0])}, {float(t[1])}, {float(t[2])}"
        )
    _atomic_write_text(recon_dir / "trajectories.txt", "\n".join(traj) + "\n")
    return out_dir


__all__ = [
    "export_gaussian_splatting",
    "export_instant_ngp",
    "export_kapture",
    "export_nerfstudio",
]
