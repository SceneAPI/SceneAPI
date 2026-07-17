"""Pre-verification correspondence graph sidecar emitter.

Mirror of :mod:`sceneapi.server.storage.two_view_emit`, but for the **raw** matches
between every image pair as written by the matcher (sequential /
exhaustive / spatial / vocabtree). Geometric verification happens later
and is exposed separately as ``two_view_geometries.json``; debugging
"why didn't this pair match?" requires the raw correspondences.

Lives at the **reconstruction** level, next to the database, because
match state belongs to the matching DB rather than to a frozen
reconstruction snapshot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sceneapi.server.schemas.api.scene import CorrespondenceGraphFile, CorrespondencePair
from sceneapi.server.storage._atomic import write_text as _atomic_write_text


def _pair_to_schema(image_id1: int, image_id2: int, matches: Any) -> CorrespondencePair:
    """``matches`` is iterable of ``(kp_idx_1, kp_idx_2)`` tuples."""
    flat: list[tuple[int, int]] = []
    if matches is not None:
        for pair in matches:
            try:
                flat.append((int(pair[0]), int(pair[1])))
            except (TypeError, ValueError, IndexError):
                continue
    return CorrespondencePair(
        image_id1=image_id1,
        image_id2=image_id2,
        num_matches=len(flat),
        matches=flat,
    )


def export_correspondence_graph(
    pairs_iter: Any,
    out_dir: Path,
    *,
    file_name: str = "correspondence_graph.json",
) -> Path:
    """Write ``correspondence_graph.json`` into ``out_dir``.

    ``pairs_iter`` yields ``(image_id1, image_id2, matches)``; the
    ``matches`` element may be a NumPy array, a list of tuples, or
    anything else iterable. Empty pairs are skipped.
    """
    pairs: list[CorrespondencePair] = []
    for image_id1, image_id2, matches in pairs_iter:
        schema = _pair_to_schema(int(image_id1), int(image_id2), matches)
        if schema.num_matches > 0:
            pairs.append(schema)
    payload = CorrespondenceGraphFile(pairs=pairs)
    out = out_dir / file_name
    _atomic_write_text(out, payload.model_dump_json(by_alias=True, indent=2))
    return out


def iter_database_correspondences(database: Any) -> Any:
    """Walk every image pair in a ``pycolmap.Database`` that has at
    least one raw match. Yields ``(image_id1, image_id2, matches)``
    where ``matches`` is the matrix the database hands back (rows of
    ``[kp_idx_1, kp_idx_2]``). Worker-side helper — never import from
    the web layer.
    """
    image_ids = list(getattr(database, "image_ids", None) or [])
    if not image_ids:
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
                matches = database.read_matches(int(i), int(j))
            except Exception:
                continue
            if matches is None:
                continue
            try:
                if len(matches) == 0:
                    continue
            except TypeError:
                pass
            yield pair[0], pair[1], matches


__all__ = ["export_correspondence_graph", "iter_database_correspondences"]
