"""DataType <-> Format completeness gate.

The three-level model is only sound if every artifact DataType can actually be
read/written: each must be realized by at least one Format (its I/O), and no
Format may realize a DataType that does not exist. Scene-input DataTypes are
exempt -- their I/O is the ingestion API (uploads/datasets), not a serialized
artifact format. This gate fails if a DataType is added without I/O (e.g.
dense_model/splat) or a format is orphaned (e.g. a dropped DataType).
"""

from __future__ import annotations

from app.core import artifacts
from app.core import datatypes as dt


def _artifact_datatype_ids() -> set[str]:
    return {t.type_id for t in dt.CORE_DATA_TYPES if t.kind == "artifact"}


def _realized_datatype_ids() -> set[str]:
    return {
        artifacts.datatype_realized_by(f.artifact_type)
        for f in artifacts.CORE_ARTIFACT_FORMATS.values()
    }


def test_artifact_type_to_datatype_map_is_total_and_valid() -> None:
    # Every artifact_type maps to a known DataType (the Format->DataType link).
    for artifact_type in artifacts.CORE_ARTIFACT_TYPES:
        assert artifact_type in artifacts.ARTIFACT_TYPE_TO_DATATYPE, artifact_type
        assert dt.is_data_type(
            artifacts.ARTIFACT_TYPE_TO_DATATYPE[artifact_type]
        ), artifact_type


def test_every_artifact_datatype_has_io() -> None:
    missing = _artifact_datatype_ids() - _realized_datatype_ids()
    assert not missing, (
        f"artifact DataType(s) with no Format (no I/O): {sorted(missing)} -- "
        f"add a format in artifacts.py or remove the type"
    )


def test_no_orphan_formats() -> None:
    orphans = _realized_datatype_ids() - _artifact_datatype_ids()
    assert not orphans, (
        f"Format(s) realizing unknown DataType(s): {sorted(orphans)} -- "
        f"add the DataType or remove the format"
    )


def test_scene_inputs_are_ingested_not_formatted() -> None:
    # Scene inputs are provided via the API; they intentionally have no
    # artifact format. This documents the exemption and guards against a
    # format accidentally realizing a scene_input.
    scene = {t.type_id for t in dt.CORE_DATA_TYPES if t.kind == "scene_input"}
    assert scene.isdisjoint(_realized_datatype_ids())
