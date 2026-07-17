"""Locks the native named-port Processor registry."""

from __future__ import annotations

import json

from sfmapi.server.core import datatypes as dt
from sfmapi.server.core import operations as ops
from sfmapi.server.core import processors as proc


def test_all_processor_ports_reference_known_datatypes() -> None:
    for processor in proc.CORE_PROCESSORS:
        for port in (*processor.consumer.values(), *processor.supplier.values()):
            assert dt.is_data_type(port.datatype), (processor.processor_id, port)


def test_core_sfm_processors_have_named_ports() -> None:
    by_id = proc.PROCESSORS_BY_ID
    assert by_id["features"].consumer["images"].datatype == "image_sequence"
    assert by_id["features"].supplier["features"].datatype == "feature_set"
    assert by_id["matches"].consumer["features"].datatype == "feature_set"
    assert by_id["matches"].consumer["pairs"].datatype == "pair_set"
    assert by_id["map"].consumer["features"].datatype == "feature_set"
    assert by_id["map"].consumer["matches"].datatype == "match_graph"
    assert by_id["map"].supplier["model"].datatype == "sparse_model"


def test_core_processor_ids_and_aliases_are_unique() -> None:
    ids = [p.processor_id for p in proc.CORE_PROCESSORS]
    aliases = [alias for p in proc.CORE_PROCESSORS for alias in p.aliases]
    assert len(ids) == len(set(ids))
    assert len(aliases) == len(set(aliases))
    assert not (set(ids) & set(aliases))


def test_algorithm_choices_are_attributes_when_io_is_identical() -> None:
    features = proc.PROCESSORS_BY_ID["features"]
    attrs = {a.name: a for a in features.attributes}
    assert attrs["type"].type == "enum"
    assert "sift" in attrs["type"].enum
    assert "superpoint" in attrs["type"].enum


def test_operation_projection_matches_flattened_processors() -> None:
    for processor in proc.CORE_PROCESSORS:
        op = ops.OPERATIONS_BY_ID[processor.processor_id]
        assert op.consumes == processor.consumes
        assert op.produces == processor.produces
        assert op.operation_contract_dict()["op_id"] == processor.processor_id


def test_processor_contract_is_json_serializable_and_self_describing() -> None:
    payload = proc.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == proc.CONTRACT_NAME == "processors"
    ids = [p["processor_id"] for p in payload["processors"]]
    assert ids == [p.processor_id for p in proc.CORE_PROCESSORS]
    assert payload["rules"]["port_datatype"] == "singular"


def test_core_contract_does_not_import_plugin() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(proc)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
