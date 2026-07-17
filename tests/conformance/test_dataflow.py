"""Typed Data Type / Processor / Pipeline discovery and validation.

SPEC §6.8.2 is Preview tier (§1.3): conforming servers **MAY** omit
this surface entirely, so a compliance run against an external target
skips rather than fails when it is absent. The in-process reference
app always mounts and serves these routes (independently of the
``expose_preview_apis`` OpenAPI fencing flag), so this suite still
exercises them fully in CI.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

pytestmark = pytest.mark.conformance


@pytest_asyncio.fixture(autouse=True)
async def _skip_when_preview_surface_absent(conf_client) -> None:
    """§1.3 Preview: compliance suites MUST NOT require this surface."""
    resp = await conf_client.get("/v1/datatypes")
    if resp.status_code in (404, 405, 501):
        pytest.skip("typed-dataflow surface not implemented (SPEC §6.8.2 [Preview])")


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
