"""Binary wire format for dense MVS depth + normal maps.

Two thin formats keyed off the same 32-byte header so a generic
reader can dispatch on the magic.

DepthMap — ``application/x-sfm-depth-v1``
-----------------------------------------
Header (32 bytes, little-endian):

    magic:        8 bytes = b"SFMDPTH\\0"
    version:      uint32  = 1
    width:        uint32
    height:       uint32
    depth_min:    float32
    depth_max:    float32
    _pad:         uint32  = 0

Body: ``width * height`` float32 pixels, row-major (top-to-bottom).
``0.0`` is the conventional "no depth" sentinel; clients **MUST**
treat non-finite values as missing.

NormalMap — ``application/x-sfm-normal-v1``
-------------------------------------------
Same 32-byte header (magic = ``b"SFMNRM\\0\\0"``); body is ``width *
height * 3`` float32 (xyz per pixel, world-space, unit length).

Both formats are fixed-stride so HTTP ``Range`` requests can fetch
arbitrary scanline windows without parsing the body.
"""

from __future__ import annotations

import struct
from typing import BinaryIO

DEPTH_MAGIC = b"SFMDPTH\x00"
NORMAL_MAGIC = b"SFMNRM\x00\x00"
HEADER_SIZE = 32
HEADER_FMT = "<8sIIIffI"  # magic, version, w, h, dmin, dmax, _pad

DEPTH_MEDIA_TYPE = "application/x-sfm-depth-v1"
NORMAL_MEDIA_TYPE = "application/x-sfm-normal-v1"


def write_depth_header(
    fh: BinaryIO,
    *,
    width: int,
    height: int,
    depth_min: float,
    depth_max: float,
) -> None:
    fh.write(struct.pack(HEADER_FMT, DEPTH_MAGIC, 1, width, height, depth_min, depth_max, 0))


def write_normal_header(fh: BinaryIO, *, width: int, height: int) -> None:
    fh.write(struct.pack(HEADER_FMT, NORMAL_MAGIC, 1, width, height, 0.0, 0.0, 0))


def read_header(fh: BinaryIO) -> tuple[bytes, int, int, int, float, float]:
    raw = fh.read(HEADER_SIZE)
    if len(raw) != HEADER_SIZE:
        raise ValueError("short header")
    magic, version, w, h, dmin, dmax, _pad = struct.unpack(HEADER_FMT, raw)
    if magic not in (DEPTH_MAGIC, NORMAL_MAGIC):
        raise ValueError(f"bad magic: {magic!r}")
    if version != 1:
        raise ValueError(f"unknown version: {version}")
    return magic, version, w, h, dmin, dmax


def encode_depth(
    width: int, height: int, depth_min: float, depth_max: float, pixels: bytes
) -> bytes:
    """Build a depth-map blob. ``pixels`` is ``width*height*4`` bytes
    (float32 row-major). The function does not parse it — callers are
    expected to pass already-encoded float32 bytes (e.g.
    ``np.asarray(arr, np.float32).tobytes()``)."""
    if len(pixels) != width * height * 4:
        raise ValueError(
            f"depth pixel buffer is {len(pixels)} bytes; expected {width * height * 4}"
        )
    header = struct.pack(HEADER_FMT, DEPTH_MAGIC, 1, width, height, depth_min, depth_max, 0)
    return header + pixels


def encode_normal(width: int, height: int, pixels: bytes) -> bytes:
    """Build a normal-map blob. ``pixels`` is ``width*height*3*4``
    bytes (3-channel float32 row-major)."""
    if len(pixels) != width * height * 3 * 4:
        raise ValueError(
            f"normal pixel buffer is {len(pixels)} bytes; expected {width * height * 3 * 4}"
        )
    header = struct.pack(HEADER_FMT, NORMAL_MAGIC, 1, width, height, 0.0, 0.0, 0)
    return header + pixels


__all__ = [
    "DEPTH_MAGIC",
    "DEPTH_MEDIA_TYPE",
    "HEADER_FMT",
    "HEADER_SIZE",
    "NORMAL_MAGIC",
    "NORMAL_MEDIA_TYPE",
    "encode_depth",
    "encode_normal",
    "read_header",
    "write_depth_header",
    "write_normal_header",
]
