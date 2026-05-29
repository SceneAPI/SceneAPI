"""Locks the COLMAP action/command core standard (app.core.colmap_actions).

The contract pins the action namespace, the input-schema kind, the closed
category vocabulary, and the read-only / GPU classification so the served
action surface stays a known, reproducible standard rather than logic
smeared through the generic adapter.
"""

from __future__ import annotations

import json

from app.core import colmap_actions as ca


def test_namespace_and_input_schema_kind() -> None:
    assert ca.ACTION_NAMESPACE == "colmap"
    # COLMAP actions validate as CLI (options + positional), not plain json.
    assert ca.INPUT_SCHEMA_KIND == "cli"


def test_read_only_and_gpu_classification() -> None:
    assert ca.is_read_only("help")
    assert ca.is_read_only("model_analyzer")
    assert not ca.is_read_only("feature_extractor")
    # Read-only commands never need a GPU; database_cleaner is the CPU-only
    # write command that is also GPU-exempt.
    assert not ca.requires_gpu("version")
    assert not ca.requires_gpu("database_cleaner")
    assert ca.requires_gpu("patch_match_stereo")
    # GPU-exempt is exactly the read-only set plus database_cleaner.
    assert ca.GPU_EXEMPT_COMMANDS == ca.READ_ONLY_COMMANDS | {"database_cleaner"}


def test_category_for_only_returns_declared_vocabulary() -> None:
    commands = [
        "feature_extractor", "feature_importer",
        "exhaustive_matcher", "sequential_matcher", "transitive_verifier",
        "mapper", "hierarchical_mapper", "point_triangulator", "bundle_adjuster",
        "model_analyzer", "model_aligner", "image_registrator", "image_deleter",
        "patch_match_stereo", "stereo_fusion", "poisson_mesher", "delaunay_mesher",
        "database_cleaner", "database_merger",
        "help", "version", "gui",
    ]
    for command in commands:
        assert ca.category_for(command) in ca.CATEGORIES, command


def test_every_declared_category_is_reachable() -> None:
    # No dead vocabulary: each declared category is produced by some command,
    # so the contract's category list stays honest against category_for().
    reachable = {
        ca.category_for(c)
        for c in [
            "exhaustive_matcher", "feature_extractor", "mapper",
            "model_analyzer", "patch_match_stereo", "database_cleaner", "gui",
        ]
    }
    assert reachable == ca.CATEGORIES


def test_contract_dict_is_json_serializable_and_self_describing() -> None:
    payload = ca.contract_dict()
    # Round-trips through JSON (it is the cross-tier artifact).
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == ca.CONTRACT_NAME == "colmap_actions"
    assert payload["contract_schema_version"] == ca.CONTRACT_SCHEMA_VERSION
    assert payload["action_namespace"] == ca.ACTION_NAMESPACE
    assert payload["input_schema_kind"] == ca.INPUT_SCHEMA_KIND
    assert payload["categories"] == sorted(ca.CATEGORIES)
    assert payload["read_only_commands"] == sorted(ca.READ_ONLY_COMMANDS)
    assert payload["gpu_exempt_commands"] == sorted(ca.GPU_EXEMPT_COMMANDS)


_SCHEMA = {
    "options": [
        {"name": "max_num_features", "type": "integer", "required": True},
        {"name": "use_gpu", "type": "boolean"},
        {"name": "quality", "type": "string", "choices": ["low", "high"]},
    ]
}


def test_validate_cli_inputs_accepts_good_inputs() -> None:
    result = ca.validate_cli_inputs(
        "feature_extractor",
        _SCHEMA,
        {"max_num_features": 8000, "quality": "high", "positional_args": ["db"]},
    )
    assert result["valid"]
    assert result["errors"] == []
    assert result["normalized_inputs"]["max_num_features"] == 8000
    assert result["normalized_inputs"]["positional_args"] == ["db"]


def test_validate_cli_inputs_flags_unknown_required_and_type() -> None:
    # Missing required option.
    missing = ca.validate_cli_inputs("feature_extractor", _SCHEMA, {})
    assert not missing["valid"]
    assert any("missing required" in e["message"] for e in missing["errors"])
    # Unknown option + bad enum + non-integer.
    bad = ca.validate_cli_inputs(
        "feature_extractor",
        _SCHEMA,
        {"max_num_features": "not-an-int", "quality": "ultra", "nope": 1},
    )
    assert not bad["valid"]
    fields = {e["field"] for e in bad["errors"]}
    assert {"max_num_features", "quality", "nope"} <= fields


def test_split_cli_inputs_separates_options_and_positional() -> None:
    options, positional = ca.split_cli_inputs(
        {"options": {"a": 1}, "positional_args": ["x", "y"]}
    )
    assert options == {"a": 1}
    assert positional == ["x", "y"]


def test_core_contract_does_not_import_colmap_plugin() -> None:
    # A data standard, not a dependency: importing it must not pull in any
    # sfmapi_colmap* plugin package.
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(ca)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
