"""Re-export shim: the image-source contract (`ImageSourceImpl`
Protocol + `MaterializedImage`) now lives in the
:mod:`sceneio.imagesource` contract package."""

from __future__ import annotations

from sceneio.imagesource import ImageSourceImpl, MaterializedImage

__all__ = [
    "ImageSourceImpl",
    "MaterializedImage",
]
