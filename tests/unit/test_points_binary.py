from __future__ import annotations

import pytest

from sceneapi.server.schemas.points_binary import (
    HEADER_SIZE,
    RECORD_SIZE,
    Point3DRecord,
    decode_records,
    encode_all,
)

pytestmark = pytest.mark.unit


def test_round_trip() -> None:
    recs = [
        Point3DRecord(point3d_id=1, xyz=(1.0, 2.0, 3.0), rgb=(255, 0, 0), track_len=4),
        Point3DRecord(point3d_id=99, xyz=(-1.5, 0.0, 12.25), rgb=(10, 20, 30), track_len=7),
    ]
    blob = encode_all(recs, bbox_min=(-1.5, 0.0, 3.0), bbox_max=(1.0, 2.0, 12.25))
    assert len(blob) == HEADER_SIZE + RECORD_SIZE * 2
    decoded, bmin, bmax = decode_records(blob)
    assert len(decoded) == 2
    assert decoded[0].point3d_id == 1
    assert decoded[1].track_len == 7
    assert bmin == (-1.5, 0.0, 3.0)
    assert bmax == (1.0, 2.0, 12.25)


def test_record_layout_is_fixed_size() -> None:
    rec = Point3DRecord(point3d_id=1, xyz=(0, 0, 0), rgb=(0, 0, 0), track_len=0)
    blob = encode_all([rec], bbox_min=(0, 0, 0), bbox_max=(0, 0, 0))
    # Header at 0..32, record at 32..58.
    assert blob[:8] == b"SFMP3D\x00\x00"
    assert len(blob) == HEADER_SIZE + RECORD_SIZE


def test_bad_magic_rejected() -> None:
    bad = b"X" * (HEADER_SIZE + RECORD_SIZE)
    with pytest.raises(ValueError, match="magic"):
        decode_records(bad)
