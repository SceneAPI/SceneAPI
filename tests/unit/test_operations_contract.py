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


def test_operation_capability_families_are_valid_and_disjoint() -> None:
    # Each operation links to capability families in the live vocabulary -- the
    # gate that keeps the typed operation layer and the portable capability
    # vocabulary in lockstep (no operation claims a family that does not exist;
    # no family is claimed by two operations).
    from app.core.capabilities import ALL_KNOWN

    claimed: dict[str, str] = {}
    for op in ops.CORE_OPERATIONS:
        for family in op.capabilities:
            assert any(
                c == family or c.startswith(family + ".") for c in ALL_KNOWN
            ), (op.op_id, family)
            assert family not in claimed, (family, claimed[family], op.op_id)
            claimed[family] = op.op_id


def test_every_capability_is_an_operation_or_explicit_infrastructure() -> None:
    # The bidirectional gate: every capability in the live vocabulary is
    # partitioned into EXACTLY ONE of -- covered by a typed operation family,
    # or matching a declared non-pipeline (infrastructure) prefix. So a
    # capability can never be added without classifying it, and the typed
    # operation layer stays the authority for what a pipeline-data stage is.
    from app.core.capabilities import ALL_KNOWN, NON_PIPELINE_CAPABILITY_PREFIXES

    op_families = {f for op in ops.CORE_OPERATIONS for f in op.capabilities}

    def matches(cap: str, prefix: str) -> bool:
        return cap == prefix or cap.startswith(prefix + ".")

    for cap in sorted(ALL_KNOWN):
        is_op = any(matches(cap, f) for f in op_families)
        is_infra = any(matches(cap, p) for p in NON_PIPELINE_CAPABILITY_PREFIXES)
        assert is_op or is_infra, f"unclassified capability: {cap!r}"
        assert not (is_op and is_infra), f"capability {cap!r} is both op and infra"


def test_pipeline_data_operations_cover_the_sfm_families() -> None:
    families = {f for op in ops.CORE_OPERATIONS for f in op.capabilities}
    # the SfM spine + the modeled post-processing
    assert {
        "features.extract", "pairs", "matchers", "map", "ba", "triangulate",
    } <= families


def test_projection_is_produced_by_an_operation() -> None:
    produced = {t for op in ops.CORE_OPERATIONS for t in op.produces}
    assert "projection" in produced  # no longer orphaned


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
