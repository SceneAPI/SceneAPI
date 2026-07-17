"""Re-export shim: the `application/x-sfm-points-v1` binary points codec
now lives in the :mod:`sceneapi_io.points_binary` contract package."""

from __future__ import annotations

from sceneapi_io.points_binary import (
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
