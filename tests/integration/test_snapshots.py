from __future__ import annotations

from pathlib import Path

import pytest

from sfmapi.server.storage.snapshots import SnapshotStore

pytestmark = pytest.mark.integration


def test_seal_atomic_and_listable(tmp_path: Path) -> None:
    src = tmp_path / "live"
    src.mkdir()
    (src / "cameras.json").write_text('{"cameras": []}')
    (src / "images.json").write_text('{"images": []}')

    root = tmp_path / "recon"
    root.mkdir()
    store = SnapshotStore(root)
    sealed = store.seal(seq=1, source_dir=src, summary={"phase": "test"})
    assert sealed.is_dir()
    assert (sealed / "cameras.json").is_file()
    assert (sealed / ".complete").is_file()
    assert (sealed / "summary.json").is_file()

    seqs = store.list_sealed()
    assert seqs == [1]
    assert store.latest() == 1


def test_partial_dirs_excluded_from_listing(tmp_path: Path) -> None:
    root = tmp_path / "recon"
    root.mkdir()
    store = SnapshotStore(root)
    src = tmp_path / "live"
    src.mkdir()
    (src / "x.txt").write_text("ok")
    store.seal(seq=1, source_dir=src)

    fake = root / "snapshots" / "00000002"
    fake.mkdir()
    (fake / "x.txt").write_text("not sealed")
    assert store.list_sealed() == [1]


def test_gc_keeps_last_n(tmp_path: Path) -> None:
    root = tmp_path / "recon"
    root.mkdir()
    store = SnapshotStore(root)
    src = tmp_path / "live"
    src.mkdir()
    (src / "x.txt").write_text("ok")
    for s in range(1, 6):
        store.seal(seq=s, source_dir=src)
    dropped = store.gc(keep_last=3)
    assert dropped == [1, 2]
    assert store.list_sealed() == [3, 4, 5]
