"""Backend-neutral camera model registry."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CameraModelDefinition:
    model: str
    projection: str
    distortion: str
    params: tuple[str, ...] = ()
    aliases: tuple[str, ...] = field(default_factory=tuple)
    spherical: bool = False
    notes: str | None = None


CAMERA_MODELS: dict[str, CameraModelDefinition] = {
    "SIMPLE_PINHOLE": CameraModelDefinition(
        model="SIMPLE_PINHOLE",
        projection="pinhole",
        distortion="none",
        params=("f", "cx", "cy"),
    ),
    "PINHOLE": CameraModelDefinition(
        model="PINHOLE",
        projection="pinhole",
        distortion="none",
        params=("fx", "fy", "cx", "cy"),
    ),
    "SIMPLE_RADIAL": CameraModelDefinition(
        model="SIMPLE_RADIAL",
        projection="pinhole",
        distortion="radial",
        params=("f", "cx", "cy", "k1"),
    ),
    "RADIAL": CameraModelDefinition(
        model="RADIAL",
        projection="pinhole",
        distortion="radial",
        params=("f", "cx", "cy", "k1", "k2"),
    ),
    "OPENCV": CameraModelDefinition(
        model="OPENCV",
        projection="pinhole",
        distortion="opencv_brown",
        params=("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2"),
    ),
    "OPENCV_FISHEYE": CameraModelDefinition(
        model="OPENCV_FISHEYE",
        projection="fisheye",
        distortion="opencv_fisheye",
        params=("fx", "fy", "cx", "cy", "k1", "k2", "k3", "k4"),
    ),
    "FULL_OPENCV": CameraModelDefinition(
        model="FULL_OPENCV",
        projection="pinhole",
        distortion="opencv_brown",
        params=("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6"),
    ),
    "SPHERICAL": CameraModelDefinition(
        model="SPHERICAL",
        projection="spherical",
        distortion="none",
        spherical=True,
        notes="Equirectangular panorama camera; params must be empty.",
    ),
}


def list_camera_models() -> list[CameraModelDefinition]:
    return sorted(CAMERA_MODELS.values(), key=lambda item: item.model)


def get_camera_model(model: str) -> CameraModelDefinition | None:
    return CAMERA_MODELS.get(model)
