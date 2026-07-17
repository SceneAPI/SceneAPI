"""Locks the DataType registry core contract (sceneapi.server.core.datatypes)."""

from __future__ import annotations

import json

from sceneapi.server.core import datatypes as dt


def test_type_ids_are_unique_and_well_kinded() -> None:
    ids = [t.type_id for t in dt.CORE_DATA_TYPES]
    assert len(ids) == len(set(ids)), "duplicate type_id"
    for t in dt.CORE_DATA_TYPES:
        assert t.kind in dt.DATA_TYPE_KINDS, (t.type_id, t.kind)
        assert t.title
        assert t.description
    assert dt.CORE_DATA_TYPES_BY_ID.keys() == set(ids)


def test_pipeline_vocabulary_present() -> None:
    by_kind: dict[str, set[str]] = {}
    for t in dt.CORE_DATA_TYPES:
        by_kind.setdefault(t.kind, set()).add(t.type_id)
    assert {"image_sequence", "camera", "camera_collection"} <= by_kind["scene_input"]
    assert {
        "feature_set",
        "pair_set",
        "match_graph",
        "sparse_model",
        "projection",
    } <= by_kind["artifact"]


def test_contract_dict_is_json_serializable_and_self_describing() -> None:
    payload = dt.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == dt.CONTRACT_NAME == "datatypes"
    assert payload["contract_schema_version"] == dt.CONTRACT_SCHEMA_VERSION
    assert [t["type_id"] for t in payload["types"]] == [t.type_id for t in dt.CORE_DATA_TYPES]


def test_core_contract_does_not_import_plugin() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(dt)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
