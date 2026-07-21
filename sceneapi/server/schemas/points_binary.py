"""Re-export shim: the `application/x-sfm-points-v1` binary points codec
now lives in the :mod:`sceneio.points_binary` contract package."""

from __future__ import annotations

from sceneio.points_binary import (
    HEADER_FMT,
    HEADER_SIZE,
    MAGIC,
    RECORD_FMT,
    RECORD_SIZE,
    Point3DRecord,
    decode_records,
    encode_all,
    read_header,
    read_record,
    write_header,
    write_record,
)

__all__ = [
    "HEADER_FMT",
    "HEADER_SIZE",
    "MAGIC",
    "RECORD_FMT",
    "RECORD_SIZE",
    "Point3DRecord",
    "decode_records",
    "encode_all",
    "read_header",
    "read_record",
    "write_header",
    "write_record",
]
