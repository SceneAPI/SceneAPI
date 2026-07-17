"""Small helpers for optional dependency detection."""

from __future__ import annotations

from importlib.util import find_spec


def has_pillow() -> bool:
    """Return whether Pillow is importable in this environment."""
    try:
        return find_spec("PIL") is not None
    except (ImportError, ValueError):
        return False


def has_opencv() -> bool:
    """Return whether OpenCV's Python bindings are importable."""
    try:
        return find_spec("cv2") is not None
    except (ImportError, ValueError):
        return False
