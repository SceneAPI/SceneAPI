"""Vectorized image projection utilities for backend-neutral pixel transforms."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal, cast

import numpy as np
import numpy.typing as npt

from sfmapi.server.core.errors import CapabilityUnavailableError, ValidationError
from sfmapi.server.core.optional_deps import has_opencv, has_pillow
from sfmapi.server.core.projections import (
    CUBEMAP_FACE_AXES,
    CUBEMAP_FACE_ORDER,
    projection_capability,
)

ImageArray = npt.NDArray[np.uint8]
FloatArray = npt.NDArray[np.float32]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def has_projection_engine() -> bool:
    """Return whether image IO dependencies for the built-in cubemap engine are present."""
    return has_opencv() or has_pillow()


def project_image_directory(
    *,
    operation: str,
    input_image_path: Path,
    output_path: Path,
    spec: dict[str, object],
) -> dict[str, object]:
    """Run a portable pixel-only projection over an image directory."""
    if operation in {"cubemap_to_equirectangular", "equirectangular_to_perspective"}:
        raise CapabilityUnavailableError(
            capability=projection_capability(operation),
            reason=(
                "This projection is a contract-only path in sfmapi core; "
                "register a backend that advertises the capability."
            ),
        )
    if operation != "equirectangular_to_cubemap":
        raise ValidationError(f"unsupported projection operation: {operation}")
    if not has_projection_engine():
        raise CapabilityUnavailableError(
            capability=projection_capability(operation),
            reason="Install sfmapi[projection] or sfmapi[image-processing] for pixel projection.",
        )
    output_path.mkdir(parents=True, exist_ok=True)
    return _equirectangular_to_cubemap(input_image_path, output_path, spec)


def _iter_images(root: Path) -> list[Path]:
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
    files.sort()
    return files


def _read_image(path: Path) -> ImageArray:
    try:
        cv2 = __import__("cv2")

        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValidationError(f"could not decode image: {path}")
        if image.ndim == 3 and image.shape[2] >= 3:
            code = cv2.COLOR_BGRA2RGBA if image.shape[2] == 4 else cv2.COLOR_BGR2RGB
            image = cv2.cvtColor(image, code)
        return cast(ImageArray, image)
    except ImportError:
        pass
    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            return np.asarray(im.convert("RGB"), dtype=np.uint8)
    except ImportError as exc:
        raise CapabilityUnavailableError(
            capability="projection.equirectangular_to_cubemap",
            reason="Install opencv-python-headless or Pillow for image projection.",
        ) from exc


def _write_image(path: Path, image: ImageArray, *, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cv2 = __import__("cv2")

        out = image
        if image.ndim == 3 and image.shape[2] >= 3:
            code = cv2.COLOR_RGBA2BGRA if image.shape[2] == 4 else cv2.COLOR_RGB2BGR
            out = cv2.cvtColor(image, code)
        params: list[int] = []
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        if not cv2.imwrite(str(path), out, params):
            raise ValidationError(f"could not write image: {path}")
        return
    except ImportError:
        pass
    try:
        from PIL import Image as PILImage

        im = PILImage.fromarray(image)
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            im.save(path, quality=quality)
        else:
            im.save(path)
    except ImportError as exc:
        raise CapabilityUnavailableError(
            capability="projection.equirectangular_to_cubemap",
            reason="Install opencv-python-headless or Pillow for image projection.",
        ) from exc


def _output_suffix(source: Path, output: dict[str, object]) -> str:
    fmt = str(output.get("format") or "source")
    if fmt == "source":
        suffix = source.suffix.lower()
        return suffix if suffix in IMAGE_EXTENSIONS else ".png"
    if fmt == "jpg":
        return ".jpg"
    if fmt == "png":
        return ".png"
    if fmt == "webp":
        return ".webp"
    raise ValidationError(f"unsupported projection output format: {fmt}")


def _int_option(value: object, *, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float, str)):
        return int(value)
    return default


def _sample_image(
    image: ImageArray,
    x: FloatArray,
    y: FloatArray,
    mode: str,
    *,
    wrap_x: bool,
) -> ImageArray:
    height, width = image.shape[:2]
    x = (np.mod(x, width) if wrap_x else np.clip(x, 0, width - 1)).astype(np.float32)
    y = np.clip(y, 0, height - 1).astype(np.float32)
    if mode == "nearest":
        xi = np.rint(x).astype(np.int64) % width
        yi = np.clip(np.rint(y).astype(np.int64), 0, height - 1)
        return cast(ImageArray, image[yi, xi])

    x0 = np.floor(x).astype(np.int64) % width
    y0 = np.floor(y).astype(np.int64)
    x1 = (x0 + 1) % width
    y1 = np.clip(y0 + 1, 0, height - 1)
    wx = (x - x0).astype(np.float32)
    wy = (y - y0).astype(np.float32)
    top = (
        image[y0, x0].astype(np.float32) * (1.0 - wx[..., None])
        + image[y0, x1].astype(np.float32) * wx[..., None]
    )
    bottom = (
        image[y1, x0].astype(np.float32) * (1.0 - wx[..., None])
        + image[y1, x1].astype(np.float32) * wx[..., None]
    )
    sampled = top * (1.0 - wy[..., None]) + bottom * wy[..., None]
    return cast(ImageArray, np.clip(sampled, 0, 255).astype(np.uint8))


def _rays_to_equirectangular_uv(
    rays: FloatArray, *, width: int, height: int
) -> tuple[FloatArray, FloatArray]:
    rays = rays / np.linalg.norm(rays, axis=-1, keepdims=True)
    lon = np.arctan2(rays[..., 0], rays[..., 2])
    lat_down = np.arcsin(np.clip(rays[..., 1], -1.0, 1.0))
    x = (lon / (2.0 * math.pi) + 0.5) * width - 0.5
    y = (lat_down / math.pi + 0.5) * height - 0.5
    return cast(FloatArray, x.astype(np.float32)), cast(FloatArray, y.astype(np.float32))


def _face_rays(face: str, face_size: int) -> FloatArray:
    coords = (np.arange(face_size, dtype=np.float32) + 0.5) / face_size * 2.0 - 1.0
    u, v = np.meshgrid(coords, coords)
    axes = CUBEMAP_FACE_AXES[face]
    forward = np.asarray(axes["forward"], dtype=np.float32)
    right = np.asarray(axes["right"], dtype=np.float32)
    down = np.asarray(axes["down"], dtype=np.float32)
    rays = forward + u[..., None] * right + v[..., None] * down
    return cast(FloatArray, rays)


def _source_image_ref(path: Path, root: Path, image: ImageArray) -> dict[str, object]:
    return {
        "name": path.relative_to(root).as_posix(),
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "camera_model": "SPHERICAL",
        "projection": "equirectangular",
    }


def _equirectangular_to_cubemap(
    input_root: Path, output_root: Path, spec: dict[str, object]
) -> dict[str, object]:
    face_size = _int_option(spec.get("face_size"), default=1024)
    sampling = cast(dict[str, object], spec.get("sampling") or {})
    output = cast(dict[str, object], spec.get("output") or {})
    interpolation = str(sampling.get("interpolation") or "linear")
    if interpolation not in {"nearest", "linear"}:
        raise ValidationError(
            "built-in projection engine supports interpolation='nearest' or 'linear'; "
            "use a backend for higher-order sampling"
        )
    quality = _int_option(output.get("jpeg_quality"), default=92)
    source_images: list[dict[str, object]] = []
    output_images: list[dict[str, object]] = []
    for source in _iter_images(input_root):
        image = _read_image(source)
        source_images.append(_source_image_ref(source, input_root, image))
        suffix = _output_suffix(source, output)
        stem = source.relative_to(input_root).with_suffix("").as_posix().replace("/", "__")
        for face in cast(list[str], spec.get("face_order") or list(CUBEMAP_FACE_ORDER)):
            rays = _face_rays(face, face_size)
            x, y = _rays_to_equirectangular_uv(rays, width=image.shape[1], height=image.shape[0])
            face_image = _sample_image(image, x, y, interpolation, wrap_x=True)
            name = f"{stem}__{face}{suffix}"
            _write_image(output_root / name, face_image, quality=quality)
            output_images.append(
                {
                    "name": name,
                    "source_name": source.relative_to(input_root).as_posix(),
                    "width": face_size,
                    "height": face_size,
                    "camera_model": "PINHOLE",
                    "params": [float(face_size), float(face_size / 2), float(face_size / 2)],
                    "projection_role": "cubemap_face",
                    "face": face,
                    "axes": CUBEMAP_FACE_AXES[face],
                }
            )
    return _engine_result(
        operation="equirectangular_to_cubemap",
        source_images=source_images,
        output_images=output_images,
        dataset_name=cast(str | None, output.get("dataset_name")),
        create_dataset=bool(output.get("create_dataset", True)),
        camera_model="PINHOLE",
        intrinsics_mode="per_image",
        is_spherical=False,
        rig_config={
            "kind": "cubemap",
            "convention": spec.get("convention", "sfmapi-opencv"),
            "face_order": spec.get("face_order", list(CUBEMAP_FACE_ORDER)),
            "face_axes": CUBEMAP_FACE_AXES,
        },
    )


def _engine_result(
    *,
    operation: str,
    source_images: list[dict[str, object]],
    output_images: list[dict[str, object]],
    dataset_name: str | None,
    create_dataset: bool,
    camera_model: str,
    intrinsics_mode: Literal["single_camera", "per_image", "per_folder"],
    is_spherical: bool,
    rig_config: dict[str, object],
) -> dict[str, object]:
    result: dict[str, object] = {
        "engine": "sfmapi.core.projection_engine",
        "operation": operation,
        "source_images": source_images,
        "output_images": output_images,
    }
    if create_dataset:
        result["derived_dataset"] = {
            "name": dataset_name,
            "camera_model": camera_model,
            "intrinsics_mode": intrinsics_mode,
            "is_spherical": is_spherical,
            "rig_config": rig_config,
        }
    if operation == "equirectangular_to_cubemap":
        result["face_axes"] = CUBEMAP_FACE_AXES
    return result


__all__ = ["has_projection_engine", "project_image_directory"]
