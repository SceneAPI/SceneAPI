"""Locks the pipeline composition core contract (app.core.pipelines).

The type-availability validator: a pipeline is valid iff each operation's
inputs are produced upstream (or supplied initially). Canonical pipelines
type-check; missing inputs and unknown operations are caught; multi-input
operations are satisfied by any upstream stage, not just the predecessor.
"""

from __future__ import annotations

import json

from app.core import pipelines as pl
from app.core import processors as proc
from app.core.attributes import Attribute
from app.services import dataflow_registry_service


def test_canonical_pipelines_type_check() -> None:
    for name, steps in pl.CANONICAL_PIPELINES.items():
        errors = pl.validate_pipeline(list(steps))
        assert errors == [], (name, [e.message for e in errors])


def test_effective_registry_resolves_core_canonical_pipeline() -> None:
    registry = dataflow_registry_service.effective_registry(manifests=[])

    pipeline = registry.pipeline_for("sfm")

    assert pipeline == {
        "pipeline_id": "sfm",
        "kind": "legacy_canonical",
        "steps": ["features", "pairs", "matches", "verify", "map"],
    }


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


def test_mapping_requires_verified_match_graph() -> None:
    errors = pl.validate_pipeline(["features", "pairs", "matches", "map"])

    assert [e.reason for e in errors] == ["unverified_match_graph"]
    assert "requires verified match_graph" in errors[0].message


def test_unknown_operation_is_reported() -> None:
    errors = pl.validate_pipeline(["features", "frobnicate"])
    assert any("unknown operation 'frobnicate'" in e.message for e in errors)


def test_initial_inputs_gate_the_first_stage() -> None:
    # With no images supplied, even `features` fails.
    errors = pl.validate_pipeline(["features"], initial_inputs=())
    assert len(errors) == 1
    assert "missing input(s): image_sequence" in errors[0].message


def test_named_port_pipeline_with_explicit_wires_type_checks() -> None:
    errors = pl.validate_pipeline([
        pl.PipelineStep(ref="extract", processor="features"),
        pl.PipelineStep(ref="pair", processor="pairs",
                        wires={"features": "extract.features"}),
        pl.PipelineStep(ref="match", processor="matches", wires={
            "features": "extract.features",
            "pairs": "pair.pairs",
        }),
        pl.PipelineStep(ref="verify", processor="verify",
                        wires={"matches": "match.matches"}),
        pl.PipelineStep(ref="map", processor="map", wires={
            "features": "extract.features",
            "matches": "verify.matches",
        }),
    ])
    assert errors == []


def test_named_port_pipeline_reports_ambiguous_inference() -> None:
    errors = pl.validate_pipeline([
        pl.PipelineStep(ref="extract", processor="features"),
        pl.PipelineStep(ref="pair", processor="pairs",
                        wires={"features": "extract.features"}),
        pl.PipelineStep(ref="match", processor="matches", wires={
            "features": "extract.features",
            "pairs": "pair.pairs",
        }),
        pl.PipelineStep(ref="verify", processor="verify",
                        wires={"matches": "match.matches"}),
        pl.PipelineStep(ref="map", processor="map",
                        wires={"features": "extract.features"}),
    ])
    assert [e.reason for e in errors] == ["ambiguous_input"]
    assert errors[0].path == "steps.4.wires.matches"


def test_named_port_pipeline_validates_attributes() -> None:
    errors = pl.validate_pipeline([
        pl.PipelineStep(
            ref="extract",
            processor="features",
            attributes={"type": "bogus"},
        ),
    ])
    assert [e.reason for e in errors] == ["invalid_attribute"]
    assert errors[0].path == "steps.0.attributes.type"


def test_special_inputs_and_attributes_are_accepted_when_declared(
    monkeypatch,
) -> None:
    extended = proc.Processor(
        "demo_special",
        "Demo special",
        {"images": proc.PortSpec("image_sequence")},
        {"features": proc.PortSpec("feature_set")},
        (),
        "demo",
        capabilities=("features",),
        special_inputs={"plugin.mask": proc.PortSpec("image_sequence", required=False)},
        special_attributes=(Attribute("plugin.weight", "float", default=1.0),),
    )
    monkeypatch.setitem(proc.PROCESSORS_BY_ID, "demo_special", extended)

    errors = pl.validate_pipeline([
        pl.PipelineStep(
            ref="demo",
            processor="demo_special",
            attributes={"plugin.weight": 0.5},
            wires={
                "images": "inputs.image_sequence",
                "plugin.mask": "inputs.image_sequence",
            },
        )
    ])

    assert errors == []


def test_optional_special_input_is_not_inferred_when_omitted(
    monkeypatch,
) -> None:
    extended = proc.Processor(
        "demo_optional",
        "Demo optional",
        {"images": proc.PortSpec("image_sequence")},
        {"features": proc.PortSpec("feature_set")},
        (),
        "demo",
        capabilities=("features",),
        special_inputs={"plugin.prior": proc.PortSpec("feature_set", required=False)},
    )
    monkeypatch.setitem(proc.PROCESSORS_BY_ID, "demo_optional", extended)

    steps = [
        pl.PipelineStep(ref="extract", processor="features"),
        pl.PipelineStep(ref="demo", processor="demo_optional"),
    ]

    assert pl.validate_pipeline(steps) == []
    assert pl.inferred_step_dependencies(steps) == {
        "extract": [],
        "demo": [],
    }


def test_plugin_processor_lookup_can_extend_pipeline_validation() -> None:
    radiance = proc.Processor(
        "radiance.train",
        "Radiance training",
        {"model": proc.PortSpec("sparse_model")},
        {"field": proc.PortSpec("radiance_field")},
        (Attribute("method", "enum", enum=("splat",)),),
        "plugin processor",
        capabilities=("radiance.train",),
    )

    errors = pl.validate_pipeline(
        [
            pl.PipelineStep(
                ref="train",
                processor="radiance.train",
                attributes={"method": "splat"},
            )
        ],
        initial_inputs=("sparse_model",),
        processor_lookup=lambda processor_id: (
            radiance if processor_id == "radiance.train"
            else proc.processor_for(processor_id)
        ),
    )

    assert errors == []


def test_merge_requires_at_least_two_models() -> None:
    errors = pl.validate_pipeline([
        pl.PipelineStep(ref="extract", processor="features"),
        pl.PipelineStep(ref="pair", processor="pairs",
                        wires={"features": "extract.features"}),
        pl.PipelineStep(ref="match", processor="matches", wires={
            "features": "extract.features",
            "pairs": "pair.pairs",
        }),
        pl.PipelineStep(ref="verify", processor="verify",
                        wires={"matches": "match.matches"}),
        pl.PipelineStep(ref="map", processor="map", wires={
            "features": "extract.features",
            "matches": "verify.matches",
        }),
        pl.PipelineStep(ref="merge", processor="merge",
                        wires={"model": ["map.model"]}),
    ])
    assert [e.reason for e in errors] == ["invalid_fan_in"]
    assert "requires at least two inputs" in errors[0].message
    assert errors[0].path == "steps.5.wires.model"


def test_duplicate_initial_inputs_do_not_satisfy_multi_input_ports() -> None:
    errors = pl.validate_pipeline(
        [pl.PipelineStep(ref="merge", processor="merge")],
        initial_inputs=("sparse_model", "sparse_model"),
    )

    assert any(e.reason == "duplicate_initial_input" for e in errors)
    assert any(e.reason == "invalid_fan_in" for e in errors)


def test_merge_rejects_duplicate_explicit_model_refs() -> None:
    errors = pl.validate_pipeline([
        pl.PipelineStep(ref="extract", processor="features"),
        pl.PipelineStep(ref="pair", processor="pairs",
                        wires={"features": "extract.features"}),
        pl.PipelineStep(ref="match", processor="matches", wires={
            "features": "extract.features",
            "pairs": "pair.pairs",
        }),
        pl.PipelineStep(ref="verify", processor="verify",
                        wires={"matches": "match.matches"}),
        pl.PipelineStep(ref="map", processor="map", wires={
            "features": "extract.features",
            "matches": "verify.matches",
        }),
        pl.PipelineStep(ref="merge", processor="merge",
                        wires={"model": ["map.model", "map.model"]}),
    ])

    assert [e.reason for e in errors] == ["invalid_fan_in"]
    assert "does not accept duplicate inputs" in errors[0].message


def test_merge_rejects_duplicate_inside_otherwise_valid_fan_in() -> None:
    errors = pl.validate_pipeline([
        pl.PipelineStep(ref="extract", processor="features"),
        pl.PipelineStep(ref="pair", processor="pairs",
                        wires={"features": "extract.features"}),
        pl.PipelineStep(ref="match", processor="matches", wires={
            "features": "extract.features",
            "pairs": "pair.pairs",
        }),
        pl.PipelineStep(ref="verify", processor="verify",
                        wires={"matches": "match.matches"}),
        pl.PipelineStep(ref="map_a", processor="map", wires={
            "features": "extract.features",
            "matches": "verify.matches",
        }),
        pl.PipelineStep(ref="map_b", processor="map", wires={
            "features": "extract.features",
            "matches": "verify.matches",
        }),
        pl.PipelineStep(ref="merge", processor="merge",
                        wires={"model": ["map_a.model", "map_a.model", "map_b.model"]}),
    ])

    assert [e.reason for e in errors] == ["invalid_fan_in"]
    assert "does not accept duplicate inputs" in errors[0].message


def test_legacy_merge_requires_at_least_two_models() -> None:
    errors = pl.validate_pipeline(["features", "pairs", "matches", "verify", "map", "merge"])
    assert [e.reason for e in errors] == ["invalid_fan_in"]
    assert "requires at least two inputs" in errors[0].message


def test_inferred_dependencies_match_validated_port_graph() -> None:
    steps = [
        pl.PipelineStep(ref="extract", processor="features"),
        pl.PipelineStep(ref="pair", processor="pairs",
                        wires={"features": "extract.features"}),
        pl.PipelineStep(ref="match", processor="matches", wires={
            "features": "extract.features",
            "pairs": "pair.pairs",
        }),
        pl.PipelineStep(ref="verify", processor="verify",
                        wires={"matches": "match.matches"}),
        pl.PipelineStep(ref="map", processor="map", wires={
            "features": "extract.features",
            "matches": "verify.matches",
        }),
    ]
    assert pl.validate_pipeline(steps) == []
    assert pl.inferred_step_dependencies(steps) == {
        "extract": [],
        "pair": ["extract"],
        "match": ["extract", "pair"],
        "verify": ["match"],
        "map": ["extract", "verify"],
    }


def test_inferred_input_source_does_not_create_task_dependency() -> None:
    steps = [
        pl.PipelineStep(ref="extract", processor="features"),
        pl.PipelineStep(ref="project", processor="project",
                        wires={"images": "inputs.image_sequence"}),
    ]
    assert pl.validate_pipeline(steps) == []
    assert pl.inferred_step_dependencies(steps) == {
        "extract": [],
        "project": [],
    }


def test_contract_dict_is_json_serializable_and_self_describing() -> None:
    payload = pl.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == pl.CONTRACT_NAME == "pipelines"
    assert set(payload["canonical_pipelines"]) == set(pl.CANONICAL_PIPELINES)
    assert payload["initial_inputs"] == list(pl.DEFAULT_INITIAL_INPUTS)
    assert "unknown_attribute" in payload["validation_reasons"]
    assert "missing_required_attribute" in payload["validation_reasons"]


def test_core_contract_does_not_import_plugin() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(pl)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
