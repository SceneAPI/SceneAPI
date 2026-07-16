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
            assert any(c == family or c.startswith(family + ".") for c in ALL_KNOWN), (
                op.op_id,
                family,
            )
            assert family not in claimed, (family, claimed[family], op.op_id)
            claimed[family] = op.op_id


def test_every_capability_is_an_operation_or_explicit_infrastructure() -> None:
    # The bidirectional gate: every capability in the live vocabulary is
    # partitioned into EXACTLY ONE of -- covered by a typed operation family
    # (positive: operation_for_capability resolves it), or a positively-declared
    # infrastructure family. Both sides are positive APIs; neither module
    # reimplements prefix logic. A new capability that links to no operation and
    # whose family is undeclared fails here with an actionable message.
    from app.core.capabilities import (
        ALL_KNOWN,
        capability_family,
        is_infrastructure_capability,
    )

    for cap in sorted(ALL_KNOWN):
        is_op = ops.operation_for_capability(cap) is not None
        is_infra = is_infrastructure_capability(cap)
        assert is_op or is_infra, (
            f"unclassified capability {cap!r} (family {capability_family(cap)!r}): "
            f"link it to an operation in app.core.operations, or declare its "
            f"family in INFRASTRUCTURE_CAPABILITY_FAMILIES"
        )
        assert not (is_op and is_infra), (
            f"capability {cap!r} is both an operation and infrastructure"
        )


def test_pipeline_data_operations_cover_the_sfm_families() -> None:
    families = {f for op in ops.CORE_OPERATIONS for f in op.capabilities}
    # the SfM spine + the modeled post-processing
    assert {
        "features.extract",
        "pairs",
        "matchers",
        "map",
        "ba",
        "triangulate",
    } <= families


def test_projection_is_produced_by_an_operation() -> None:
    produced = {t for op in ops.CORE_OPERATIONS for t in op.produces}
    assert "projection" in produced  # no longer orphaned


def test_every_artifact_datatype_is_produced_by_an_operation() -> None:
    # The dual of the I/O gate: an artifact type must be producible by the
    # pipeline (some operation produces it), else it could never appear in a
    # run. Scene inputs (image_sequence, camera, camera_collection) are EXEMPT
    # -- they are provided/optional calibration inputs, not pipeline outputs.
    from app.core import datatypes as dt

    produced = {t for op in ops.CORE_OPERATIONS for t in op.produces}
    artifact_types = {t.type_id for t in dt.CORE_DATA_TYPES if t.kind == "artifact"}
    missing = artifact_types - produced
    assert not missing, (
        f"artifact DataType(s) no operation produces: {sorted(missing)} -- "
        f"add a producing operation or make it a scene_input"
    )


def test_config_stage_links_to_a_valid_param_stage() -> None:
    # An operation's config_stage points at the backend config-schema stage
    # carrying its parameters (the algorithm knobs). When set it must be a real
    # config stage; the SfM spine + refine carry parameters.
    from app.core.config_stages import VALID_CONFIG_STAGES

    for op in ops.CORE_OPERATIONS:
        if op.config_stage is not None:
            assert op.config_stage in VALID_CONFIG_STAGES, (op.op_id, op.config_stage)
    staged = {op.op_id for op in ops.CORE_OPERATIONS if op.config_stage}
    assert {"features", "pairs", "matches", "verify", "map", "refine"} <= staged


def test_operation_for_capability_inverse() -> None:
    assert ops.operation_for_capability("features.extract.sift") == "features"
    assert ops.operation_for_capability("map.incremental") == "map"
    assert ops.operation_for_capability("ba.standard") == "refine"
    assert ops.operation_for_capability("matches.verify") == "verify"
    # infrastructure capabilities map to no operation
    assert ops.operation_for_capability("projects.crud") is None
    assert ops.operation_for_capability("backend.actions") is None


def test_operations_for_provider_capability_set() -> None:
    # the typed view of a provider, derived from its capability set
    caps = {
        "features.extract.sift",
        "matchers.superglue",
        "map.incremental",
        "projects.crud",  # infra -- ignored
    }
    assert ops.operations_for_capabilities(caps) == {"features", "matches", "map"}


def test_post_processing_operations_preserve_sparse_model() -> None:
    # E8 decision (accept): post-processing operations are sparse_model ->
    # sparse_model by design -- a reconstruction's identity is its DataType, and
    # refined/merged/georegistered are provenance, not distinct types. This pins
    # the decision so a future "distinguish the outputs" change is deliberate.
    post = {
        "triangulate",
        "refine",
        "optimize_poses",
        "relocalize",
        "merge",
        "georegister",
        "undistort",
    }
    for op_id in post:
        op = ops.OPERATIONS_BY_ID[op_id]
        assert "sparse_model" in op.consumes, op_id
        assert op.produces == ("sparse_model",), op_id


def test_contract_dict_is_json_serializable_and_self_describing() -> None:
    payload = ops.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == ops.CONTRACT_NAME == "operations"
    assert [o["op_id"] for o in payload["operations"]] == [op.op_id for op in ops.CORE_OPERATIONS]


def test_core_contract_does_not_import_plugin() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(ops)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
