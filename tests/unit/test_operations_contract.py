"""Locks the operation registry core contract (app.core.operations)."""

from __future__ import annotations

import json

from app.core import datatypes as dt
from app.core import operations as ops


def test_all_edges_reference_known_datatypes() -> None:
    for op in ops.CORE_OPERATIONS:
        for type_id in (*op.consumes, *op.produces):
            assert dt.is_data_type(type_id), (op.op_id, type_id)


def test_core_sfm_operations_present_and_typed() -> None:
    by_id = ops.OPERATIONS_BY_ID
    assert {"features", "pairs", "matches", "verify", "map"} <= by_id.keys()
    assert by_id["features"].consumes == ("image_sequence",)
    assert by_id["features"].produces == ("feature_set",)
    # matches and map are multi-input -- the reason a pipeline is a typed DAG.
    assert by_id["matches"].consumes == ("feature_set", "pair_set")
    assert by_id["map"].consumes == ("feature_set", "match_graph")


def test_contract_dict_is_json_serializable_and_self_describing() -> None:
    payload = ops.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == ops.CONTRACT_NAME == "operations"
    assert [o["op_id"] for o in payload["operations"]] == [
        op.op_id for op in ops.CORE_OPERATIONS
    ]


def test_core_contract_does_not_import_plugin() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(ops)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
