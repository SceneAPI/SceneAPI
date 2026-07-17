from __future__ import annotations

import os
from pathlib import Path

import pytest

from sceneapi.server.sources.local import LocalPathSource

pytestmark = pytest.mark.integration


def _make_jpeg(p: Path, size: int = 1024) -> None:
    p.write_bytes(b"\xff\xd8\xff\xe0" + os.urandom(size - 4))


def test_local_source_lists_images(tmp_path: Path) -> None:
    img1 = tmp_path / "a.jpg"
    img2 = tmp_path / "b.png"
    junk = tmp_path / "readme.txt"
    _make_jpeg(img1)
    _make_jpeg(img2)
    junk.write_text("ignore me")

    src = LocalPathSource(root=tmp_path)
    mats = src.materialize()
    names = sorted(m.name for m in mats)
    assert names == ["a.jpg", "b.png"]
    for m in mats:
        assert m.abs_path.is_file()


def test_local_source_fingerprint_stable(tmp_path: Path) -> None:
    _make_jpeg(tmp_path / "a.jpg")
    _make_jpeg(tmp_path / "b.jpg")
    src = LocalPathSource(root=tmp_path)
    fp1 = src.fingerprint()
    fp2 = src.fingerprint()
    assert fp1 == fp2


def test_local_source_fingerprint_changes_on_mutation(tmp_path: Path) -> None:
    p = tmp_path / "a.jpg"
    _make_jpeg(p)
    src = LocalPathSource(root=tmp_path)
    fp1 = src.fingerprint()
    # Mutate content (different bytes, set mtime forward to be safe).
    _make_jpeg(p, size=2048)
    os.utime(p, (p.stat().st_atime + 1, p.stat().st_mtime + 1))
    fp2 = src.fingerprint()
    assert fp1 != fp2


def test_local_source_no_copy_for_large_file(tmp_path: Path) -> None:
    # Sparse file simulating "user has a 50GB dir".
    big = tmp_path / "big.jpg"
    with big.open("wb") as fh:
        fh.write(b"\xff\xd8\xff\xe0")
        fh.seek(50 * 1024 * 1024 - 1)
        fh.write(b"\0")
    src = LocalPathSource(root=tmp_path)
    mats = src.materialize()
    assert len(mats) == 1
    assert mats[0].abs_path == big
    fp = src.fingerprint()
    assert "files" in fp
    assert len(fp["files"]) == 1
