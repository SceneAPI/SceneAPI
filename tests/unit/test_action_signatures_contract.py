"""Locks the action-signature core contract (app.core.action_signatures).

Pins the morphism axis of the typed dataflow: every signature's consumes/
produces edges must reference known DataTypes, and the core SfM pipeline
must type-compose end to end.
"""

from __future__ import annotations

import json

from app.core import action_signatures as sig
from app.core import datatypes as dt


def test_all_edges_reference_known_datatypes() -> None:
    for s in sig.CORE_ACTION_SIGNATURES:
        for type_id in (*s.consumes, *s.produces):
            assert dt.is_data_type(type_id), (s.action_id, type_id)


def test_signature_lookup() -> None:
    fe = sig.signature_for("colmap.feature_extractor")
    assert fe is not None
    assert fe.consumes == ("image_sequence",)
    assert fe.produces == ("feature_set",)
    assert sig.signature_for("colmap.help") is None  # unsignatured


def test_core_sfm_pipeline_type_composes() -> None:
    # feature_extractor -> matcher -> mapper -> bundle_adjuster must chain:
    # each stage's produced type satisfies the next stage's consumed type.
    chain = [
        "colmap.feature_extractor",
        "colmap.exhaustive_matcher",
        "colmap.mapper",
        "colmap.bundle_adjuster",
    ]
    sigs = [sig.signature_for(a) for a in chain]
    assert all(sigs)
    for upstream, downstream in zip(sigs, sigs[1:]):
        assert set(upstream.produces) & set(downstream.consumes), (
            upstream.action_id, downstream.action_id,
        )


def test_contract_dict_is_json_serializable_and_self_describing() -> None:
    payload = sig.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == sig.CONTRACT_NAME == "action_signatures"
    ids = [row["action_id"] for row in payload["signatures"]]
    assert ids == [s.action_id for s in sig.CORE_ACTION_SIGNATURES]


def test_core_contract_does_not_import_plugin() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(sig)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
