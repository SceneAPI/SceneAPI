"""Conversion semantics in the typed model (G4).

Two kinds, per the design:

* cross-FORMAT coercion -- same DataType, different serialization (e.g.
  ``feature_set[h5] -> feature_set[colmap_db]``). Type-preserving, so the chain
  type-check never sees it; the I/O layer (``resolve_io_formats``) offers the
  format options and the backend (``ArtifactConversionBackend``) coerces at
  execution. There is no separate core registry -- a DataType's formats already
  enumerate the coercible representations.
* cross-TYPE conversion -- ``DataType A -> B``. An ordinary operation; nominal
  typing means the validator requires it to be PRESENT, never bridged
  implicitly.
"""

from __future__ import annotations

from app.core import artifacts, pipelines


def test_cross_format_coercion_is_type_preserving() -> None:
    # A DataType may have several formats; a coercion between any two of them
    # preserves the type, so it is invisible to the chain type-check.
    formats = artifacts.resolve_io_formats("feature_set")
    assert len(formats) >= 1
    assert all(f.datatype == "feature_set" for f in formats)
    # All serialize the SAME DataType -> A->B between them never changes the type.


def test_cross_type_bridge_must_be_explicit() -> None:
    # Nominal typing: a type break is rejected (no implicit coercion)...
    broken = pipelines.validate_pipeline(["features", "map"])
    assert broken and "match_graph" in broken[0].message
    # ...and is bridged only by inserting the operations that PRODUCE the
    # missing type (matches/verify produce match_graph) -- an explicit step.
    assert pipelines.validate_pipeline(
        ["features", "pairs", "matches", "verify", "map"]
    ) == []
