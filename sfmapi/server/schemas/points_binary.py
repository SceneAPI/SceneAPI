"""Binary points wire format — `application/x-sfm-points-v1`.

Header (44 bytes, little-endian):
  magic:    8 bytes = b"SFMP3D\\x00\\x00"
  version:  uint32  = 1
  count:    uint64
  bbox_min: 3 x float32 (12 B)
  bbox_max: 3 x float32 (12 B)

Each record (26 bytes, little-endian):
  xyz:        3 x float32  (12)
  rgb:        3 x uint8    (3)
  _pad:       1 x uint8    (1)
  track_len:  uint16       (2)
  point3d_id: uint64       (8)

Records are written in ascending point3d_id order so that HTTP `Range`
requests can treat the file as a fixed-stride array.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO

MAGIC = b"SFMP3D\x00\x00"
HEADER_SIZE = 44
RECORD_SIZE = 26
HEADER_FMT = "<8sIQffffff"  # 6 floats = bbox_min[3] + bbox_max[3]
RECORD_FMT = "<fffBBBBHQ"


@dataclass(frozen=True)
class Point3DRecord:
    point3d_id: int
    xyz: tuple[float, float, float]
    rgb: tuple[int, int, int]
    track_len: int


def write_header(
    fh: BinaryIO,
    *,
    count: int,
    bbox_min: tuple[float, float, float],
    bbox_max: tuple[float, float, float],
) -> None:
    fh.write(struct.pack(HEADER_FMT, MAGIC, 1, count, *bbox_min, *bbox_max))


def write_record(fh: BinaryIO, rec: Point3DRecord) -> None:
    fh.write(
        struct.pack(
            RECORD_FMT,
            rec.xyz[0],
            rec.xyz[1],
            rec.xyz[2],
            rec.rgb[0] & 0xFF,
            rec.rgb[1] & 0xFF,
            rec.rgb[2] & 0xFF,
            0,
            rec.track_len & 0xFFFF,
            rec.point3d_id & 0xFFFFFFFFFFFFFFFF,
        )
    )


def read_header(fh: BinaryIO) -> tuple[int, tuple[float, float, float], tuple[float, float, float]]:
    raw = fh.read(HEADER_SIZE)
    if len(raw) != HEADER_SIZE:
        raise ValueError("short header")
    magic, version, count, b0, b1, b2, b3, b4, b5 = struct.unpack(HEADER_FMT, raw)
    if magic != MAGIC:
        raise ValueError("bad magic")
    if version != 1:
        raise ValueError(f"unknown version: {version}")
    return count, (b0, b1, b2), (b3, b4, b5)


def read_record(buf: bytes, offset: int = 0) -> Point3DRecord:
    x, y, z, r, g, b, _pad, tl, pid = struct.unpack_from(RECORD_FMT, buf, offset)
    return Point3DRecord(point3d_id=pid, xyz=(x, y, z), rgb=(r, g, b), track_len=tl)


def encode_all(
    records: Iterable[Point3DRecord],
    *,
    bbox_min: tuple[float, float, float],
    bbox_max: tuple[float, float, float],
) -> bytes:
    rec_list = list(records)
    out = bytearray(HEADER_SIZE + RECORD_SIZE * len(rec_list))
    struct.pack_into(HEADER_FMT, out, 0, MAGIC, 1, len(rec_list), *bbox_min, *bbox_max)
    for i, rec in enumerate(rec_list):
        struct.pack_into(
            RECORD_FMT,
            out,
            HEADER_SIZE + i * RECORD_SIZE,
            rec.xyz[0],
            rec.xyz[1],
            rec.xyz[2],
            rec.rgb[0] & 0xFF,
            rec.rgb[1] & 0xFF,
            rec.rgb[2] & 0xFF,
            0,
            rec.track_len & 0xFFFF,
            rec.point3d_id & 0xFFFFFFFFFFFFFFFF,
        )
    return bytes(out)


def decode_records(
    buf: bytes,
) -> tuple[list[Point3DRecord], tuple[float, float, float], tuple[float, float, float]]:
    if len(buf) < HEADER_SIZE:
        raise ValueError("buffer too small for header")
    import io

    fh = io.BytesIO(buf)
    count, bmin, bmax = read_header(fh)
    body = buf[HEADER_SIZE:]
    if len(body) < count * RECORD_SIZE:
        raise ValueError("buffer too small for records")
    out = []
    for i in range(count):
        out.append(read_record(body, i * RECORD_SIZE))
    return out, bmin, bmax
