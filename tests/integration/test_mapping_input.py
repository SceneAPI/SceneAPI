from __future__ import annotations

from pathlib import Path

import pytest

from sfmapi.server.core.errors import StorageError
from sfmapi.server.storage.mapping_input import (
    gc_checkpoints,
    latest_checkpoint,
    list_checkpoints,
    write_checkpoint,
)

pytestmark = pytest.mark.integration


def test_round_trip(tmp_path: Path) -> None:
    write_checkpoint(tmp_path, seq=1, payload=b"PCMAPIN\x00data1", summary={"phase": "init"})
    write_checkpoint(tmp_path, seq=2, payload=b"PCMAPIN\x00data2", summary={"phase": "iter1"})
    cps = list_checkpoints(tmp_path)
    assert [c.seq for c in cps] == [1, 2]
    assert cps[1].path.read_bytes() == b"PCMAPIN\x00data2"
    assert cps[1].summary["phase"] == "iter1"
    assert latest_checkpoint(tmp_path).seq == 2


def test_duplicate_seq_rejected(tmp_path: Path) -> None:
    write_checkpoint(tmp_path, seq=1, payload=b"x")
    with pytest.raises(StorageError, match="already exists"):
        write_checkpoint(tmp_path, seq=1, payload=b"y")


def test_gc_keeps_last_n(tmp_path: Path) -> None:
    for i in range(1, 8):
        write_checkpoint(tmp_path, seq=i, payload=f"p{i}".encode())
    dropped = gc_checkpoints(tmp_path, keep_last=3)
    assert dropped == [1, 2, 3, 4]
    cps = list_checkpoints(tmp_path)
    assert [c.seq for c in cps] == [5, 6, 7]
