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


def test_link_or_copy_raises_when_copy_fallback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workers._materialize as mod

    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"payload")

    def fail_link(_src: Path, _dst: Path) -> None:
        raise OSError("link failed")

    def fail_copy(_src: Path, _dst: Path) -> None:
        raise OSError("copy failed")

    monkeypatch.setattr(mod.os, "link", fail_link)
    monkeypatch.setattr(mod.shutil, "copy2", fail_copy)

    with pytest.raises(OSError, match="copy failed"):
        link_or_copy(src, dst)


def test_materialize_local_links_declared_rel_paths_into_stage(tmp_path: Path) -> None:
    src_root = tmp_path / "images"
    nested = src_root / "nested"
    nested.mkdir(parents=True)
    (src_root / "a.jpg").write_bytes(b"a")
    (nested / "b-source.jpg").write_bytes(b"b")
    stage = tmp_path / "stage"
    root, names = materialize_image_set(
        {
            "kind": "local",
            "image_root": str(src_root),
            "image_list": ["a.jpg", "b.jpg"],
            "rel_paths": {"b.jpg": "nested/b-source.jpg"},
        },
        stage,
    )
    assert root == stage
    assert names == ["a.jpg", "b.jpg"]
    assert (stage / "a.jpg").read_bytes() == b"a"
    assert (stage / "b.jpg").read_bytes() == b"b"


def test_materialize_local_rejects_rel_path_escape(tmp_path: Path) -> None:
    src_root = tmp_path / "images"
    src_root.mkdir()
    (tmp_path / "outside.jpg").write_bytes(b"outside")

    with pytest.raises(ValidationError, match="rel_path must stay"):
        materialize_image_set(
            {
                "kind": "local",
                "image_root": str(src_root),
                "image_list": ["a.jpg"],
                "rel_paths": {"a.jpg": "../outside.jpg"},
            },
            tmp_path / "stage",
        )


def test_materialize_rejects_stage_name_escape(tmp_path: Path) -> None:
    src_root = tmp_path / "images"
    src_root.mkdir()
    (src_root / "a.jpg").write_bytes(b"a")

    with pytest.raises(ValidationError, match="image name must stay"):
        materialize_image_set(
            {
                "kind": "local",
                "image_root": str(src_root),
                "image_list": ["../a.jpg"],
                "rel_paths": {"../a.jpg": "a.jpg"},
            },
            tmp_path / "stage",
        )


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


def test_resolve_image_path_local_uses_rel_paths(tmp_path: Path) -> None:
    root = tmp_path / "imgs"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "x-source.jpg").write_bytes(b"x")
    p = resolve_image_path(
        "x.jpg",
        {
            "kind": "local",
            "image_root": str(root),
            "rel_paths": {"x.jpg": "nested/x-source.jpg"},
        },
        tmp_path / "stage",
    )
    assert p == nested / "x-source.jpg"


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
