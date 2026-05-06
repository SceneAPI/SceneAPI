"""Shared image materialization helper."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.workers._materialize import (
    link_or_copy,
    materialize_image_set,
    resolve_image_path,
)

pytestmark = pytest.mark.unit


def test_link_or_copy_creates_dst_and_is_idempotent(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    dst = tmp_path / "sub" / "dst.bin"
    src.write_bytes(b"payload")
    link_or_copy(src, dst)
    assert dst.is_file()
    assert dst.read_bytes() == b"payload"
    # Second call must be a no-op (no exception even though dst exists).
    link_or_copy(src, dst)
    assert dst.read_bytes() == b"payload"


def test_materialize_local_returns_root_without_copying(tmp_path: Path) -> None:
    src_root = tmp_path / "images"
    src_root.mkdir()
    (src_root / "a.jpg").write_bytes(b"a")
    (src_root / "b.jpg").write_bytes(b"b")
    stage = tmp_path / "stage"
    root, names = materialize_image_set(
        {"kind": "local", "image_root": str(src_root), "image_list": ["a.jpg", "b.jpg"]},
        stage,
    )
    assert root == src_root
    assert names == ["a.jpg", "b.jpg"]
    # Stage must NOT have been populated for local sources.
    assert not stage.exists()


def test_materialize_upload_links_blobs_into_stage(tmp_path: Path, monkeypatch) -> None:
    from app.storage.blobs import InMemoryBlobStore

    bs = InMemoryBlobStore()
    sha_a, _ = bs.put_bytes(b"AAA")
    sha_b, _ = bs.put_bytes(b"BBB")

    import app.workers._materialize as mod

    monkeypatch.setattr(mod, "get_blob_store", lambda: bs)

    stage = tmp_path / "stage"
    root, names = materialize_image_set(
        {
            "kind": "upload",
            "image_list": ["a.jpg", "b.jpg"],
            "blob_shas": {"a.jpg": sha_a, "b.jpg": sha_b},
        },
        stage,
    )
    assert root == stage
    assert names == ["a.jpg", "b.jpg"]
    assert (stage / "a.jpg").read_bytes() == b"AAA"
    assert (stage / "b.jpg").read_bytes() == b"BBB"
    bs.shutdown()


def test_materialize_rejects_empty_image_list(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="no images"):
        materialize_image_set({"kind": "upload", "image_list": []}, tmp_path / "x")


def test_materialize_unknown_kind_raises(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="unknown materialization"):
        materialize_image_set({"kind": "magic", "image_list": ["a.jpg"]}, tmp_path / "x")


def test_resolve_image_path_local_returns_existing(tmp_path: Path) -> None:
    root = tmp_path / "imgs"
    root.mkdir()
    (root / "x.jpg").write_bytes(b"x")
    p = resolve_image_path("x.jpg", {"kind": "local", "image_root": str(root)}, tmp_path / "stage")
    assert p == root / "x.jpg"


def test_resolve_image_path_returns_none_for_missing(tmp_path: Path) -> None:
    p = resolve_image_path(
        "missing.jpg",
        {"kind": "local", "image_root": str(tmp_path / "nope")},
        tmp_path / "stage",
    )
    assert p is None


def test_resolve_image_path_upload_returns_none_when_sha_missing(
    tmp_path: Path,
) -> None:
    p = resolve_image_path(
        "a.jpg",
        {"kind": "upload", "blob_shas": {}},
        tmp_path / "stage",
    )
    assert p is None
