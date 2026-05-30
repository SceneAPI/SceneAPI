"""Locks the pipeline composition core contract (app.core.pipelines).

Pins the nominal chain/DAG type-checker and the canonical pipelines: valid
chains pass, type breaks are caught, and bridging a mismatch requires an
explicit conversion (never implicit).
"""

from __future__ import annotations

import json

from app.core import pipelines as pl


def test_canonical_pipelines_type_compose() -> None:
    for name, steps in pl.CANONICAL_PIPELINES.items():
        errors = pl.validate_linear(list(steps))
        assert errors == [], (name, [e.message for e in errors])


def test_linear_type_break_is_caught() -> None:
    # feature_extractor produces feature_set; mapper consumes match_graph ->
    # no shared type, must report a break (the matcher is missing).
    errors = pl.validate_linear(["colmap.feature_extractor", "colmap.mapper"])
    assert len(errors) == 1
    assert "no shared type" in errors[0].message


def test_unsignatured_action_is_reported() -> None:
    errors = pl.validate_linear(["colmap.help", "colmap.mapper"])
    assert any("no declared signature" in e.message for e in errors)


def test_dag_validation_checks_each_typed_edge() -> None:
    # point_triangulator consumes BOTH sparse_reconstruction and match_graph.
    nodes = [
        {"node_id": "m", "action_id": "colmap.exhaustive_matcher"},
        {"node_id": "map", "action_id": "colmap.mapper"},
        {"node_id": "tri", "action_id": "colmap.point_triangulator"},
    ]
    edges = [
        {"src": "m", "dst": "map", "type_id": "match_graph"},
        {"src": "map", "dst": "tri", "type_id": "sparse_reconstruction"},
        {"src": "m", "dst": "tri", "type_id": "match_graph"},
    ]
    assert pl.validate_chain(nodes, edges) == []


def test_dag_rejects_a_type_the_source_does_not_produce() -> None:
    nodes = [
        {"node_id": "fe", "action_id": "colmap.feature_extractor"},
        {"node_id": "map", "action_id": "colmap.mapper"},
    ]
    # feature_extractor produces feature_set, not match_graph.
    edges = [{"src": "fe", "dst": "map", "type_id": "match_graph"}]
    errors = pl.validate_chain(nodes, edges)
    assert len(errors) == 1
    assert "does not produce" in errors[0].message


def test_contract_dict_is_json_serializable_and_self_describing() -> None:
    payload = pl.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == pl.CONTRACT_NAME == "pipelines"
    assert set(payload["canonical_pipelines"]) == set(pl.CANONICAL_PIPELINES)


def test_core_contract_does_not_import_plugin() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(pl)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
