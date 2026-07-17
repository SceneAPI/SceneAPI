"""CorrespondenceGraph emitter — synthetic stub tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sceneapi.server.schemas.api.scene import CorrespondenceGraphFile
from sceneapi.server.storage.correspondence_emit import (
    export_correspondence_graph,
    iter_database_correspondences,
)

pytestmark = pytest.mark.unit


def test_export_writes_pairs(tmp_path: Path) -> None:
    out = export_correspondence_graph(
        [(1, 2, [(0, 5), (3, 8), (10, 12)]), (1, 3, [(0, 1)])], tmp_path
    )
    parsed = CorrespondenceGraphFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert len(parsed.pairs) == 2
    p12 = next(p for p in parsed.pairs if p.image_id2 == 2)
    assert p12.num_matches == 3
    assert p12.matches == [(0, 5), (3, 8), (10, 12)]


def test_export_skips_empty_pairs(tmp_path: Path) -> None:
    out = export_correspondence_graph([(1, 2, []), (1, 3, [(0, 0)])], tmp_path)
    body = json.loads(out.read_text(encoding="utf-8"))
    assert len(body["pairs"]) == 1
    assert body["pairs"][0]["image_id2"] == 3


def test_export_handles_invalid_rows(tmp_path: Path) -> None:
    out = export_correspondence_graph([(1, 2, [(0, 5), ("bad", 1), (3, 4)])], tmp_path)
    parsed = CorrespondenceGraphFile.model_validate_json(out.read_text(encoding="utf-8"))
    # The "bad" row was skipped silently.
    assert parsed.pairs[0].matches == [(0, 5), (3, 4)]
    assert parsed.pairs[0].num_matches == 2


def test_export_empty_iter(tmp_path: Path) -> None:
    out = export_correspondence_graph([], tmp_path)
    parsed = CorrespondenceGraphFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert parsed.pairs == []


class _StubDatabase:
    """Mimics pycolmap.Database for the iter helper. Yields matches for
    a few hand-picked pairs and raises for others (which iter must
    swallow)."""

    def __init__(self) -> None:
        self.image_ids = [1, 2, 3, 4]

    def read_matches(self, i: int, j: int):
        if (i, j) == (1, 2):
            return [(0, 1), (2, 3)]
        if (i, j) == (2, 3):
            return [(5, 6)]
        if (i, j) == (3, 4):
            raise RuntimeError("no matches in DB")
        return []  # empty pair — should be skipped


def test_iter_database_correspondences_yields_only_nonempty(tmp_path: Path) -> None:
    db = _StubDatabase()
    pairs = list(iter_database_correspondences(db))
    assert (1, 2, [(0, 1), (2, 3)]) in [(a, b, list(m)) for a, b, m in pairs]
    assert any(p[:2] == (2, 3) for p in pairs)
    # (3, 4) raised → swallowed; (1, 3) etc. were empty → skipped.
    assert all(p[:2] != (3, 4) for p in pairs)
    assert all(p[:2] != (1, 3) for p in pairs)


def test_schema_round_trip_preserves_match_count() -> None:
    body = {
        "pairs": [
            {
                "image_id1": 7,
                "image_id2": 9,
                "num_matches": 2,
                "matches": [[0, 1], [2, 3]],
            }
        ]
    }
    parsed = CorrespondenceGraphFile.model_validate(body)
    assert parsed.pairs[0].num_matches == 2
    assert parsed.pairs[0].matches == [(0, 1), (2, 3)]
