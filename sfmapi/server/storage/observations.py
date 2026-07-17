"""Observations + visibility sidecars in a sealed snapshot.

Two JSON files live next to ``points.bin``:

  - ``observations_by_image.json``: per-image-id list of
    :class:`ImageObservationRow` dicts.
  - ``observations_by_point.json``: per-point3d-id list of
    :class:`PointObservationRow` dicts.

Both files are optional in the snapshot and **MAY** be missing on a
freshly-sealed snapshot whose worker did not emit them. The API
endpoints return 404 in that case.

The two row shapes are defined in
:mod:`sfmapi.server.schemas.api.reconstructions` and re-exported here under
their canonical wire names so the storage layer and the wire layer
share one definition (closes the audit-2026 wire/storage duplication
smell — see ``L24`` in ``docs/guides/decisions.md``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from sfmapi.server.schemas.api.reconstructions import (
    ImageObservationRow,
    PointObservationRow,
)

__all__ = [
    "OBS_BY_IMAGE",
    "OBS_BY_POINT",
    "ImageObservationRow",
    "PointObservationRow",
    "has_observations",
    "has_visibility",
    "read_observations_for_image",
    "read_visibility_for_point",
    "write_observations_by_image",
    "write_observations_by_point",
]

OBS_BY_IMAGE = "observations_by_image.json"
OBS_BY_POINT = "observations_by_point.json"


def _row_to_dict(row: ImageObservationRow | PointObservationRow) -> dict:
    """Serialize an observation row, dropping ``error=None`` to match
    the historical sidecar shape (``error`` is omitted when absent)."""
    return row.model_dump(exclude_none=True)


def _path(snapshot_dir: Path, name: str) -> Path:
    return snapshot_dir / name


def write_observations_by_image(
    snapshot_dir: Path,
    *,
    by_image: dict[int | str, Iterable[ImageObservationRow]],
) -> Path:
    payload = {str(k): [_row_to_dict(o) for o in obs] for k, obs in by_image.items()}
    p = _path(snapshot_dir, OBS_BY_IMAGE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return p


def write_observations_by_point(
    snapshot_dir: Path,
    *,
    by_point: dict[int | str, Iterable[PointObservationRow]],
) -> Path:
    payload = {str(k): [_row_to_dict(o) for o in obs] for k, obs in by_point.items()}
    p = _path(snapshot_dir, OBS_BY_POINT)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return p


def read_observations_for_image(snapshot_dir: Path, image_id: str) -> list[dict] | None:
    p = _path(snapshot_dir, OBS_BY_IMAGE)
    if not p.is_file():
        return None
    body = json.loads(p.read_text(encoding="utf-8"))
    return body.get(str(image_id))


def read_visibility_for_point(snapshot_dir: Path, point3d_id: str) -> list[dict] | None:
    p = _path(snapshot_dir, OBS_BY_POINT)
    if not p.is_file():
        return None
    body = json.loads(p.read_text(encoding="utf-8"))
    return body.get(str(point3d_id))


def has_observations(snapshot_dir: Path) -> bool:
    return _path(snapshot_dir, OBS_BY_IMAGE).is_file()


def has_visibility(snapshot_dir: Path) -> bool:
    return _path(snapshot_dir, OBS_BY_POINT).is_file()
