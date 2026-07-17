"""Re-export shim: the image-source contract (`ImageSourceImpl`
Protocol + `MaterializedImage`) now lives in the
:mod:`sceneapi_io.imagesource` contract package."""

from __future__ import annotations

from sceneapi_io.imagesource import ImageSourceImpl, MaterializedImage

__all__ = [
    "ImageSourceImpl",
    "MaterializedImage",
]
