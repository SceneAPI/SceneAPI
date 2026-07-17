"""Portable projection conventions for spherical image utilities."""

from __future__ import annotations

from typing import Final

SCENEAPI_CUBEMAP_CONVENTION: Final[str] = "sfmapi-opencv"
"""Default cubemap convention.

Face cameras use OpenCV image axes: x right, y down, z forward.
"""

CUBEMAP_FACE_ORDER: Final[tuple[str, ...]] = (
    "front",
    "right",
    "back",
    "left",
    "up",
    "down",
)

CUBEMAP_FACE_AXES: Final[dict[str, dict[str, list[int]]]] = {
    "front": {"forward": [0, 0, 1], "right": [1, 0, 0], "down": [0, 1, 0]},
    "right": {"forward": [1, 0, 0], "right": [0, 0, -1], "down": [0, 1, 0]},
    "back": {"forward": [0, 0, -1], "right": [-1, 0, 0], "down": [0, 1, 0]},
    "left": {"forward": [-1, 0, 0], "right": [0, 0, 1], "down": [0, 1, 0]},
    "up": {"forward": [0, -1, 0], "right": [1, 0, 0], "down": [0, 0, 1]},
    "down": {"forward": [0, 1, 0], "right": [1, 0, 0], "down": [0, 0, -1]},
}

PROJECTION_CAPABILITIES: Final[dict[str, str]] = {
    "equirectangular_to_cubemap": "projection.equirectangular_to_cubemap",
    "cubemap_to_equirectangular": "projection.cubemap_to_equirectangular",
    "equirectangular_to_perspective": "projection.equirectangular_to_perspective",
}


def projection_capability(operation: str) -> str:
    """Return the portable capability required for a projection operation."""
    return PROJECTION_CAPABILITIES[operation]


__all__ = [
    "CUBEMAP_FACE_AXES",
    "CUBEMAP_FACE_ORDER",
    "PROJECTION_CAPABILITIES",
    "SCENEAPI_CUBEMAP_CONVENTION",
    "projection_capability",
]
