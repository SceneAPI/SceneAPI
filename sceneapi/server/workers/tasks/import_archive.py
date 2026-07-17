"""Decode an uploaded image zip into a derived dataset.

The zip rides the normal chunked-upload protocol; only the *unpack* is
new. The archive bytes are read straight from the blob store — for the
in-memory blob backend (ephemeral / demo mode) that is a ``BytesIO``,
so the zip is decoded directly in memory and never spills to a second
tempfile. For the FS / S3 backends ``.open()`` is the already-persisted
file, so there is still no second copy.

Zip-bomb defense: the *uncompressed* total is summed from the central
directory (``ZipInfo.file_size``) and checked against
``settings.archive_import_max_bytes`` BEFORE any entry is decompressed.
Zip-slip defense: every extracted path is resolved and asserted to land
inside the per-task output dir.

The task emits the generic ``derived_dataset`` block; on task success
the dispatcher hands it to ``dataset_service.register_derived_dataset``,
which turns it into ImageSource + Dataset + Image rows (dimensions read
from disk there), so this task owns only the unpack — not the DB
bookkeeping.
"""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path

from sceneapi.server.core.config import get_settings
from sceneapi.server.core.errors import ValidationError
from sceneapi.server.core.projection_engine import IMAGE_EXTENSIONS
from sceneapi.server.db.models import Task
from sceneapi.server.storage.blobs import get_blob_store
from sceneapi.server.workers._task_io import read_state
from sceneapi.server.workers.tasks._registry import task_handler


def _is_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_EXTENSIONS


def _is_traversal(name: str) -> bool:
    """A zip entry whose path is absolute, drive-anchored, or contains a
    ``..`` segment is hostile. Reject such an archive outright rather
    than silently relocating the entry — the common-prefix strip would
    otherwise basename a ``../../evil.jpg`` into a safe-looking name and
    mask the attack."""
    norm = name.replace("\\", "/")
    if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
        return True
    return ".." in norm.split("/")


def _select_entries(zf: zipfile.ZipFile, *, prefix: str) -> list[zipfile.ZipInfo]:
    selected: list[zipfile.ZipInfo] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if prefix and not name.startswith(prefix):
            continue
        if not _is_image(name):
            continue
        selected.append(info)
    return selected


def _strip_root(names: list[str], *, prefix: str) -> str:
    """The directory to make registered names relative to.

    An explicit ``prefix`` wins (the caller told us where the images are
    rooted). Otherwise auto-detect: the common directory of every
    selected entry, so a COLMAP zip's ``south-building/images/P1.JPG``
    registers as ``P1.JPG`` while a genuinely multi-dir zip keeps its
    disambiguating segments. A single entry strips its own directory so
    it registers by basename either way.
    """
    if prefix:
        return prefix.rstrip("/")
    if len(names) == 1:
        return posixpath.dirname(names[0])
    common = posixpath.commonpath(names)
    # commonpath can return an actual entry when every name shares one
    # full path; only treat it as a strippable *directory*.
    if any(n == common for n in names):
        common = posixpath.dirname(common)
    return common


def _registered_name(archive_name: str, strip_root: str) -> str:
    rel = posixpath.relpath(archive_name, strip_root) if strip_root else archive_name
    return rel.replace("\\", "/")


@task_handler("import_archive")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    blob_sha = str(inputs["blob_sha"])
    output_dir = Path(inputs["output_dir"]).resolve()
    prefix = str(spec.get("image_prefix") or "")
    max_bytes = int(get_settings().archive_import_max_bytes)

    output_dir.mkdir(parents=True, exist_ok=True)
    store = get_blob_store()
    if not store.exists(blob_sha):
        raise ValidationError(f"archive blob {blob_sha} not found")

    with store.open(blob_sha) as fh:
        try:
            zf = zipfile.ZipFile(fh)
        except zipfile.BadZipFile as exc:
            raise ValidationError(f"uploaded blob is not a valid zip: {exc}") from exc
        with zf:
            for info in zf.infolist():
                if not info.is_dir() and _is_traversal(info.filename):
                    raise ValidationError(f"refusing to extract path escape: {info.filename!r}")
            entries = _select_entries(zf, prefix=prefix)
            if not entries:
                hint = f" under prefix {prefix!r}" if prefix else ""
                raise ValidationError(
                    f"archive contains no image files{hint} (looked for {sorted(IMAGE_EXTENSIONS)})"
                )

            # Zip-bomb guard: reject on the declared uncompressed total
            # before decompressing a single byte. 0 disables the cap.
            uncompressed = sum(info.file_size for info in entries)
            if max_bytes and uncompressed > max_bytes:
                raise ValidationError(
                    f"archive uncompressed image total {uncompressed} bytes exceeds "
                    f"the {max_bytes}-byte cap (SCENEAPI_ARCHIVE_IMPORT_MAX_BYTES)"
                )

            names = [info.filename for info in entries]
            strip_root = _strip_root(names, prefix=prefix)

            image_items: list[dict[str, str]] = []
            for info in entries:
                rel = _registered_name(info.filename, strip_root)
                target = (output_dir / rel).resolve()
                try:
                    target.relative_to(output_dir)
                except ValueError as exc:
                    raise ValidationError(
                        f"refusing to extract path escape: {info.filename!r}"
                    ) from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    # Bounded copy — never trust ZipInfo.file_size for
                    # the loop, just stream until EOF.
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                image_items.append({"name": rel})

    return {
        "num_images": len(image_items),
        "derived_dataset": {
            "name": spec.get("name"),
            "camera_model": str(spec.get("camera_model") or "SIMPLE_RADIAL"),
            "intrinsics_mode": str(spec.get("intrinsics_mode") or "single_camera"),
            "is_spherical": bool(spec.get("is_spherical", False)),
            "rig_config": spec.get("rig_config")
            if isinstance(spec.get("rig_config"), dict)
            else None,
            "source_dataset_id": None,
            "root": str(output_dir),
            "images": image_items,
        },
    }
