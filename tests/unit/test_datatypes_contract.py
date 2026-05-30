"""Locks the DataType registry core contract (app.core.datatypes).

Pins the logical-object axis of the typed dataflow: the type ids, their
kinds, and the bridge from the legacy artifacts.py ``artifact_type`` set.
"""

from __future__ import annotations

import json

from app.core import datatypes as dt


def test_type_ids_are_unique_and_well_kinded() -> None:
    ids = [t.type_id for t in dt.CORE_DATA_TYPES]
    assert len(ids) == len(set(ids)), "duplicate type_id"
    for t in dt.CORE_DATA_TYPES:
        assert t.kind in dt.DATA_TYPE_KINDS, (t.type_id, t.kind)
        assert t.title and t.description
    assert dt.CORE_DATA_TYPES_BY_ID.keys() == set(ids)


def test_scene_inputs_and_artifacts_present() -> None:
    by_kind: dict[str, set[str]] = {}
    for t in dt.CORE_DATA_TYPES:
        by_kind.setdefault(t.kind, set()).add(t.type_id)
    # The scene-input types named in the design.
    assert {"image_sequence", "camera", "camera_collection"} <= by_kind["scene_input"]
    # The artifact types promoted from artifacts.py.
    assert {
        "feature_set", "pair_spec", "match_graph", "sparse_reconstruction",
        "projection",
    } <= by_kind["artifact"]


def test_artifact_type_aliases_resolve_to_known_datatypes() -> None:
    for artifact_type, type_id in dt.ARTIFACT_TYPE_TO_DATATYPE.items():
        assert dt.is_data_type(type_id), (artifact_type, type_id)
        assert dt.datatype_for_artifact_type(artifact_type) == type_id


def test_every_artifacts_format_type_maps_to_a_datatype() -> None:
    # The bridge must cover every artifact_type artifacts.py actually uses, so
    # no format realizes a type the DataType registry does not know.
    from app.core import artifacts

    used = {f.artifact_type for f in artifacts.CORE_ARTIFACT_FORMATS.values()}
    used |= set(artifacts.CORE_ARTIFACT_TYPES)
    for artifact_type in sorted(used):
        assert artifact_type in dt.ARTIFACT_TYPE_TO_DATATYPE, artifact_type
        assert dt.is_data_type(dt.ARTIFACT_TYPE_TO_DATATYPE[artifact_type])


def test_contract_dict_is_json_serializable_and_self_describing() -> None:
    payload = dt.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == dt.CONTRACT_NAME == "datatypes"
    assert payload["contract_schema_version"] == dt.CONTRACT_SCHEMA_VERSION
    serialized_ids = [t["type_id"] for t in payload["types"]]
    assert serialized_ids == [t.type_id for t in dt.CORE_DATA_TYPES]
    assert payload["artifact_type_aliases"] == dict(
        sorted(dt.ARTIFACT_TYPE_TO_DATATYPE.items())
    )


def test_core_contract_does_not_import_plugin() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(dt)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
