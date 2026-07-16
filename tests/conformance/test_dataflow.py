"""Typed Data Type / Processor / Pipeline discovery and validation."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.conformance


async def test_dataflow_contract_endpoints_are_present(conf_client) -> None:
    datatypes = await conf_client.get("/v1/datatypes")
    assert datatypes.status_code == 200, datatypes.text
    dt_body = datatypes.json()
    assert dt_body["contract"] == "datatypes"
    assert {"image_sequence", "feature_set", "match_graph", "sparse_model"} <= {
        row["type_id"] for row in dt_body["types"]
    }

    attributes = await conf_client.get("/v1/attributes")
    assert attributes.status_code == 200, attributes.text
    attr_body = attributes.json()
    assert attr_body["contract"] == "attributes"
    assert "enum" in attr_body["attribute_types"]

    processors = await conf_client.get("/v1/processors")
    assert processors.status_code == 200, processors.text
    proc_body = processors.json()
    assert proc_body["contract"] == "processors"
    proc_by_id = {row["processor_id"]: row for row in proc_body["processors"]}
    assert proc_by_id["features"]["consumer"]["images"]["datatype"] == "image_sequence"
    assert proc_by_id["map"]["supplier"]["model"]["datatype"] == "sparse_model"

    pipelines = await conf_client.get("/v1/pipelines")
    assert pipelines.status_code == 200, pipelines.text
    pipe_body = pipelines.json()
    assert pipe_body["contract"] == "pipelines"
    assert pipe_body["plugin_pipelines"] == []


async def test_pipeline_validate_accepts_port_wired_processor_dag(conf_client) -> None:
    resp = await conf_client.post(
        "/v1/pipelines:validate",
        json={
            "steps": [
                {"ref": "extract", "processor": "features"},
                {
                    "ref": "pair",
                    "processor": "pairs",
                    "wires": {"features": "extract.features"},
                },
                {
                    "ref": "match",
                    "processor": "matches",
                    "wires": {
                        "features": "extract.features",
                        "pairs": "pair.pairs",
                    },
                },
                {
                    "ref": "verify",
                    "processor": "verify",
                    "wires": {"matches": "match.matches"},
                },
                {
                    "ref": "map",
                    "processor": "map",
                    "wires": {
                        "features": "extract.features",
                        "matches": "verify.matches",
                    },
                },
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"valid": True, "errors": []}


async def test_pipeline_validate_reports_stable_reason_and_path(conf_client) -> None:
    resp = await conf_client.post(
        "/v1/pipelines:validate",
        json={"steps": [{"processor": "features", "attributes": {"bogus": 1}}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is False
    assert body["errors"][0]["reason"] == "unknown_attribute"
    assert body["errors"][0]["path"] == "steps.0.attributes.bogus"
