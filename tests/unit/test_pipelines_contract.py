"""Locks the pipeline composition core contract (app.core.pipelines).

The type-availability validator: a pipeline is valid iff each operation's
inputs are produced upstream (or supplied initially). Canonical pipelines
type-check; missing inputs and unknown operations are caught; multi-input
operations are satisfied by any upstream stage, not just the predecessor.
"""

from __future__ import annotations

import json

from app.core import pipelines as pl


def test_canonical_pipelines_type_check() -> None:
    for name, steps in pl.CANONICAL_PIPELINES.items():
        errors = pl.validate_pipeline(list(steps))
        assert errors == [], (name, [e.message for e in errors])


def test_multi_input_op_satisfied_by_upstream_not_just_predecessor() -> None:
    # `map` consumes feature_set (from step 0) AND match_graph (from step 3);
    # the immediate predecessor (verify) only produces match_graph -- the
    # availability model must still accept it.
    assert pl.validate_pipeline(
        ["features", "pairs", "matches", "verify", "map"]) == []


def test_missing_input_is_reported() -> None:
    # map without any matching upstream: feature_set is available (features)
    # but match_graph is not.
    errors = pl.validate_pipeline(["features", "map"])
    assert len(errors) == 1
    assert "missing input(s): match_graph" in errors[0].message
    assert errors[0].where == "step 1 'map'"


def test_unknown_operation_is_reported() -> None:
    errors = pl.validate_pipeline(["features", "frobnicate"])
    assert any("unknown operation 'frobnicate'" in e.message for e in errors)


def test_initial_inputs_gate_the_first_stage() -> None:
    # With no images supplied, even `features` fails.
    errors = pl.validate_pipeline(["features"], initial_inputs=())
    assert len(errors) == 1
    assert "missing input(s): image_sequence" in errors[0].message


def test_contract_dict_is_json_serializable_and_self_describing() -> None:
    payload = pl.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == pl.CONTRACT_NAME == "pipelines"
    assert set(payload["canonical_pipelines"]) == set(pl.CANONICAL_PIPELINES)
    assert payload["initial_inputs"] == list(pl.DEFAULT_INITIAL_INPUTS)


def test_core_contract_does_not_import_plugin() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(pl)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
