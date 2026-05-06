"""Shared image materialization for worker tasks.

Three places used to hand-roll the upload/local/s3 → local-path dance:
``extract``, ``render_cubemap``, ``vlad_index`` (which then re-imported
extract's helper). This module collapses them into one place.

Conventions:
  - ``materialization`` is the dict the orchestrator hands a Task —
    keys vary by ``kind``:
      - ``upload``: ``image_list`` + ``blob_shas[name] -> sha``
      - ``local``:  ``image_list`` + ``image_root``
      - ``s3``:     ``image_list`` + ``bucket`` + ``prefix``
  - ``stage`` is a per-task scratch directory the caller owns. Local
    sources may bypass staging entirely (their root is already a real
    directory pycolmap can read).
  - Returns ``Path``s; never raises on a single-image lookup failure
    (returns None) — callers decide whether a missing image is fatal.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

from app.core.errors import ValidationError
from app.storage.blobs import get_blob_store


def link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink ``src`` to ``dst``; fall back to copy across volumes /
    on Windows. Idempotent — a no-op if ``dst`` already exists."""
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        with contextlib.suppress(OSError):
            shutil.copy2(src, dst)


def _materialize_upload(materialization: dict, image_list: list[str], stage: Path) -> None:
    bs = get_blob_store()
    blob_shas = materialization.get("blob_shas") or {}
    stage.mkdir(parents=True, exist_ok=True)
    for name in image_list:
        sha = blob_shas.get(name)
        if not sha:
            raise ValidationError(f"upload source missing blob sha for {name}")
        src = bs.local_path(sha)
        link_or_copy(src, stage / name)


def _materialize_s3(materialization: dict, image_list: list[str], stage: Path) -> None:
    from app.sources.s3 import S3Source

    bucket = materialization["bucket"]
    prefix = materialization.get("prefix", "")
    mats = S3Source(bucket=bucket, prefix=prefix).materialize()
    stage.mkdir(parents=True, exist_ok=True)
    for m in mats:
        link_or_copy(m.abs_path, stage / m.name)


def materialize_image_set(materialization: dict, stage: Path) -> tuple[Path, list[str]]:
    """Realize an entire dataset's images at a local path.

    Returns ``(image_root, image_list)`` — for local sources this is
    the source's own root (no copy); for upload/s3 it's ``stage`` after
    the bytes have been linked/copied into it.
    """
    kind = materialization.get("kind")
    image_list: list[str] = list(materialization.get("image_list") or [])
    if not image_list:
        raise ValidationError("dataset has no images")
    if kind == "local":
        root = materialization.get("image_root")
        if not root:
            raise ValidationError("local source missing image_root")
        return Path(root), image_list
    if kind == "upload":
        _materialize_upload(materialization, image_list, stage)
        return stage, image_list
    if kind == "s3":
        _materialize_s3(materialization, image_list, stage)
        return stage, image_list
    raise ValidationError(f"unknown materialization kind: {kind!r}")


def resolve_image_path(name: str, materialization: dict, stage: Path) -> Path | None:
    """Resolve a single image to a real local file path.

    Returns ``None`` when the name can't be located — used by callers
    (e.g. ``vlad_index``) that tolerate partial coverage.
    """
    kind = materialization.get("kind")
    if kind == "local":
        root = materialization.get("image_root")
        if not root:
            return None
        src = Path(root) / name
        return src if src.is_file() else None
    if kind == "upload":
        sha = (materialization.get("blob_shas") or {}).get(name)
        if not sha:
            return None
        try:
            src = get_blob_store().local_path(sha)
        except Exception:
            return None
        dst = stage / name
        link_or_copy(src, dst)
        return dst if dst.is_file() else None
    if kind == "s3":
        # For s3 we materialize the whole set lazily — cheaper than
        # head-of-line download for a single image, and S3Cache makes
        # repeated calls free.
        try:
            root, _ = materialize_image_set(materialization, stage)
        except Exception:
            return None
        candidate = root / name
        return candidate if candidate.is_file() else None
    return None
