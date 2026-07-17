"""Verified two-view geometry sidecar emitter.

Reads pycolmap's database for the verified two-view geometries between
all matched image pairs and writes ``two_view_geometries.json`` to the
reconstruction root. Lives at the **reconstruction** level (not per
sealed snapshot) because verify can run independently of mapping and
the result tracks the database state, not a frozen reconstruction.

Pycolmap is duck-typed via the database object passed in; tests pass a
synthetic stub that yields ``(image_id1, image_id2, geometry)`` tuples.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sceneapi.server.schemas.api.scene import (
    TwoViewGeometriesFile,
    TwoViewGeometry,
    TwoViewGeometryType,
)
from sceneapi.server.storage._atomic import write_text as _atomic_write_text

_TYPE_MAP: dict[int, TwoViewGeometryType] = {
    0: "undefined",
    1: "degenerate",
    2: "calibrated",
    3: "uncalibrated",
    4: "planar",
    5: "panoramic",
    6: "planar_or_panoramic",
    7: "watermark",
    8: "multiple",
}


def _flatten_3x3(m: Any) -> list[float] | None:
    if m is None:
        return None
    out: list[float] = []
    for row in m:
        for v in row:
            out.append(float(v))
    if len(out) != 9:
        return None
    return out


def _geometry_to_schema(image_id1: int, image_id2: int, geom: Any) -> TwoViewGeometry:
    type_val: TwoViewGeometryType
    raw_type = getattr(geom, "config", None) or getattr(geom, "type", None)
    if isinstance(raw_type, str):
        type_val = raw_type.lower()  # type: ignore[assignment]
    elif isinstance(raw_type, int):
        type_val = _TYPE_MAP.get(raw_type, "undefined")
    else:
        type_val = "undefined"
    inliers = []
    raw_inliers = getattr(geom, "inlier_matches", None)
    if raw_inliers is not None:
        for pair in raw_inliers:
            inliers.append((int(pair[0]), int(pair[1])))
    return TwoViewGeometry(
        image_id1=image_id1,
        image_id2=image_id2,
        type=type_val,
        num_inliers=len(inliers) or int(getattr(geom, "num_inliers", 0) or 0),
        F=_flatten_3x3(getattr(geom, "F", None)),
        E=_flatten_3x3(getattr(geom, "E", None)),
        H=_flatten_3x3(getattr(geom, "H", None)),
        inlier_matches=inliers,
    )


def export_two_view_geometries(
    pairs_iter: Any, out_dir: Path, *, file_name: str = "two_view_geometries.json"
) -> Path:
    """Write ``two_view_geometries.json`` into ``out_dir``.

    ``pairs_iter`` is an iterable of ``(image_id1, image_id2, geometry)``
    tuples — typically produced by walking ``pycolmap.Database`` for all
    image pairs and calling ``db.read_two_view_geometry(pair_id)``.
    """
    pairs: list[TwoViewGeometry] = []
    for image_id1, image_id2, geom in pairs_iter:
        pairs.append(_geometry_to_schema(int(image_id1), int(image_id2), geom))
    payload = TwoViewGeometriesFile(pairs=pairs)
    out = out_dir / file_name
    _atomic_write_text(out, payload.model_dump_json(by_alias=True, indent=2))
    return out


def iter_database_pairs(database: Any) -> Any:
    """Walk all verified pairs in a pycolmap.Database. Yields
    ``(image_id1, image_id2, geometry)``. Worker-side helper — never
    import from the web layer."""
    image_ids = list(getattr(database, "image_ids", None) or [])
    if not image_ids:
        # Fallback: use num_images to enumerate, if exposed.
        n = int(getattr(database, "num_images", 0) or 0)
        image_ids = list(range(1, n + 1))
    seen: set[tuple[int, int]] = set()
    for i in image_ids:
        for j in image_ids:
            if i >= j:
                continue
            pair = (int(i), int(j))
            if pair in seen:
                continue
            seen.add(pair)
            try:
                geom = database.read_two_view_geometry(int(i), int(j))
            except Exception:
                continue
            if geom is None:
                continue
            yield pair[0], pair[1], geom


__all__ = ["export_two_view_geometries", "iter_database_pairs"]
