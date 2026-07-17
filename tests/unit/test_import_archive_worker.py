"""import_archive worker — zip-bomb cap, zip-slip, prefix detection.

The happy path + schema + dispatcher registration are covered by the
e2e suite. These unit tests pin the worker-side safety guards that are
impractical to exercise end-to-end (a real >5 GB zip) by driving
``run()`` directly with a fake blob store.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from sceneapi.server.core.errors import ValidationError
from sceneapi.server.db.models import Task
from sceneapi.server.workers.tasks import import_archive

pytestmark = pytest.mark.unit


class _FakeStore:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def exists(self, sha: str) -> bool:
        return True

    def open(self, sha: str) -> io.BytesIO:
        return io.BytesIO(self._data)


def _task(tmp_path: Path, spec: dict[str, Any]) -> Task:
    return Task(
        task_id="01H00000000000000000000000",
        tenant_id="default",
        job_id="01H00000000000000000000001",
        kind="import_archive",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        task_state_json={
            "inputs": {"blob_sha": "a" * 64, "output_dir": str(tmp_path / "out")},
            "spec": spec,
        },
    )


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_decodes_in_memory_and_strips_common_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _zip(
        {
            "south-building/images/P1.JPG": b"\xff\xd8\xff\xd9",
            "south-building/images/P2.JPG": b"\xff\xd8\xff\xd9",
            "south-building/database.db": b"junk",
        }
    )
    monkeypatch.setattr(import_archive, "get_blob_store", lambda: _FakeStore(archive))

    result = import_archive.run(_task(tmp_path, {"name": "south"}))

    assert result["num_images"] == 2
    block = result["derived_dataset"]
    assert block["name"] == "south"
    assert block["root"] == str((tmp_path / "out").resolve())
    assert sorted(i["name"] for i in block["images"]) == ["P1.JPG", "P2.JPG"]
    # Files actually landed under the output dir with the prefix stripped.
    assert (tmp_path / "out" / "P1.JPG").is_file()
    assert not (tmp_path / "out" / "south-building").exists()


def test_rejects_zip_bomb_via_central_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The uncompressed total is checked from ZipInfo before any entry
    is decompressed — a highly compressible large member is rejected up
    front, not after it inflates in memory."""
    big = b"\x00" * (2 * 1024 * 1024)  # 2 MiB of zeros -> tiny compressed
    archive = _zip({"images/huge.png": big})
    monkeypatch.setattr(import_archive, "get_blob_store", lambda: _FakeStore(archive))
    monkeypatch.setattr(
        import_archive,
        "get_settings",
        lambda: type("S", (), {"archive_import_max_bytes": 1024})(),
    )

    with pytest.raises(ValidationError, match="exceeds the 1024-byte cap"):
        import_archive.run(_task(tmp_path, {}))
    # Nothing extracted on rejection.
    assert not (tmp_path / "out" / "huge.png").exists()


def test_cap_zero_disables_the_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _zip({"images/a.jpg": b"\x00" * 4096})
    monkeypatch.setattr(import_archive, "get_blob_store", lambda: _FakeStore(archive))
    monkeypatch.setattr(
        import_archive,
        "get_settings",
        lambda: type("S", (), {"archive_import_max_bytes": 0})(),
    )

    result = import_archive.run(_task(tmp_path, {}))
    assert result["num_images"] == 1


def test_rejects_zip_slip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # An image-extension entry whose path escapes the output dir.
        zf.writestr("../../evil.jpg", b"\xff\xd8\xff\xd9")
    monkeypatch.setattr(import_archive, "get_blob_store", lambda: _FakeStore(buf.getvalue()))

    with pytest.raises(ValidationError, match="path escape"):
        import_archive.run(_task(tmp_path, {}))


def test_rejects_archive_with_no_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _zip({"readme.txt": b"text", "data.bin": b"\x00\x01"})
    monkeypatch.setattr(import_archive, "get_blob_store", lambda: _FakeStore(archive))

    with pytest.raises(ValidationError, match="no image files"):
        import_archive.run(_task(tmp_path, {}))


def test_rejects_non_zip_blob(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        import_archive, "get_blob_store", lambda: _FakeStore(b"definitely not a zip")
    )

    with pytest.raises(ValidationError, match="not a valid zip"):
        import_archive.run(_task(tmp_path, {}))


def test_image_prefix_scopes_the_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _zip(
        {
            "cap/images/keep.jpg": b"\xff\xd8\xff\xd9",
            "cap/thumbs/skip.jpg": b"\xff\xd8\xff\xd9",
        }
    )
    monkeypatch.setattr(import_archive, "get_blob_store", lambda: _FakeStore(archive))

    result = import_archive.run(_task(tmp_path, {"image_prefix": "cap/images/"}))
    assert result["num_images"] == 1
    assert result["derived_dataset"]["images"][0]["name"] == "keep.jpg"
