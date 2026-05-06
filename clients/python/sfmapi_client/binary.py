"""Wire-format binary decoders for the sfmapi Python SDK.

Mirrors ``app/schemas/points_binary.py`` (server-side encoder) and
``app/schemas/depth_map_binary.py``. Pure-stdlib (struct + memoryview);
no NumPy required.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

POINTS_MAGIC = b"SFMP3D\x00\x00"
DEPTH_MAGIC = b"SFMDPTH\x00"
NORMAL_MAGIC = b"SFMNRM\x00\x00"

POINTS_HEADER_SIZE = 44
POINTS_RECORD_SIZE = 26
MAP_HEADER_SIZE = 32

POINTS_MEDIA_TYPE = "application/x-sfm-points-v1"
DEPTH_MEDIA_TYPE = "application/x-sfm-depth-v1"
NORMAL_MEDIA_TYPE = "application/x-sfm-normal-v1"


class WireFormatError(ValueError):
    """Raised when a binary blob fails magic / version / length checks."""


@dataclass(frozen=True)
class Point3DRecord:
    point3d_id: int
    xyz: tuple[float, float, float]
    rgb: tuple[int, int, int]
    track_len: int


@dataclass
class PointsBinary:
    count: int
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    records: list[Point3DRecord]


def parse_points_binary(data: bytes) -> PointsBinary:
    """Decode an ``application/x-sfm-points-v1`` blob."""
    if len(data) < POINTS_HEADER_SIZE:
        raise WireFormatError("points-binary: buffer too small for header")
    if data[:8] != POINTS_MAGIC:
        raise WireFormatError("points-binary: bad magic")
    version = struct.unpack_from("<I", data, 8)[0]
    if version != 1:
        raise WireFormatError(f"points-binary: unknown version {version}")
    count = struct.unpack_from("<Q", data, 12)[0]
    bbox_min = struct.unpack_from("<fff", data, 20)
    bbox_max = struct.unpack_from("<fff", data, 32)
    expected = POINTS_HEADER_SIZE + count * POINTS_RECORD_SIZE
    if len(data) < expected:
        raise WireFormatError(f"points-binary: body short — got {len(data)}, expected {expected}")
    records: list[Point3DRecord] = []
    for i in range(count):
        off = POINTS_HEADER_SIZE + i * POINTS_RECORD_SIZE
        x, y, z, r, g, b, _pad, tl, pid = struct.unpack_from("<fffBBBBHQ", data, off)
        records.append(
            Point3DRecord(
                point3d_id=pid,
                xyz=(x, y, z),
                rgb=(r, g, b),
                track_len=tl,
            )
        )
    return PointsBinary(count=count, bbox_min=bbox_min, bbox_max=bbox_max, records=records)


@dataclass
class DepthMap:
    width: int
    height: int
    depth_min: float
    depth_max: float
    pixels: bytes  # raw float32 bytes (caller decodes with struct or array.array)


def parse_depth_map(data: bytes) -> DepthMap:
    """Decode an ``application/x-sfm-depth-v1`` blob.

    ``pixels`` is the raw float32 little-endian byte buffer of length
    ``width * height * 4``. Use ``struct`` or ``array.array("f")`` to
    materialize it (or ``np.frombuffer(pixels, dtype="<f4")`` if NumPy
    is available)."""
    if len(data) < MAP_HEADER_SIZE:
        raise WireFormatError("depth-binary: buffer too small for header")
    if data[:8] != DEPTH_MAGIC:
        raise WireFormatError("depth-binary: bad magic")
    version = struct.unpack_from("<I", data, 8)[0]
    if version != 1:
        raise WireFormatError(f"depth-binary: unknown version {version}")
    w, h = struct.unpack_from("<II", data, 12)
    dmin, dmax = struct.unpack_from("<ff", data, 20)
    expected = MAP_HEADER_SIZE + w * h * 4
    if len(data) < expected:
        raise WireFormatError(f"depth-binary: body short — got {len(data)}, expected {expected}")
    pixels = bytes(data[MAP_HEADER_SIZE : MAP_HEADER_SIZE + w * h * 4])
    return DepthMap(width=w, height=h, depth_min=dmin, depth_max=dmax, pixels=pixels)


@dataclass
class NormalMap:
    width: int
    height: int
    pixels: bytes  # raw float32 bytes, length = width*height*3*4


def parse_normal_map(data: bytes) -> NormalMap:
    """Decode an ``application/x-sfm-normal-v1`` blob."""
    if len(data) < MAP_HEADER_SIZE:
        raise WireFormatError("normal-binary: buffer too small for header")
    if data[:8] != NORMAL_MAGIC:
        raise WireFormatError("normal-binary: bad magic")
    version = struct.unpack_from("<I", data, 8)[0]
    if version != 1:
        raise WireFormatError(f"normal-binary: unknown version {version}")
    w, h = struct.unpack_from("<II", data, 12)
    expected = MAP_HEADER_SIZE + w * h * 3 * 4
    if len(data) < expected:
        raise WireFormatError(f"normal-binary: body short — got {len(data)}, expected {expected}")
    pixels = bytes(data[MAP_HEADER_SIZE : MAP_HEADER_SIZE + w * h * 3 * 4])
    return NormalMap(width=w, height=h, pixels=pixels)


__all__ = [
    "DEPTH_MAGIC",
    "DEPTH_MEDIA_TYPE",
    "MAP_HEADER_SIZE",
    "NORMAL_MAGIC",
    "NORMAL_MEDIA_TYPE",
    "POINTS_HEADER_SIZE",
    "POINTS_MAGIC",
    "POINTS_MEDIA_TYPE",
    "POINTS_RECORD_SIZE",
    "DepthMap",
    "NormalMap",
    "Point3DRecord",
    "PointsBinary",
    "WireFormatError",
    "parse_depth_map",
    "parse_normal_map",
    "parse_points_binary",
]
