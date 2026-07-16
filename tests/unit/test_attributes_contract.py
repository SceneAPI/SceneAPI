"""Locks the portable Processor attribute schema contract."""

from __future__ import annotations

import json

import pytest

from app.core import attributes as attrs


def test_attribute_contract_is_json_serializable_and_self_describing() -> None:
    payload = attrs.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == attrs.CONTRACT_NAME == "attributes"
    assert {"int", "float", "bool", "str", "enum", "datatype-ref"} <= set(
        payload["attribute_types"]
    )


def test_validate_attributes_reports_stable_reasons() -> None:
    schema = (
        attrs.Attribute("method", "enum", enum=("sift", "superpoint")),
        attrs.Attribute("max_steps", "int", min=1),
    )
    errors = attrs.validate_attributes(
        schema,
        {"method": "unknown", "max_steps": 0, "extra": True},
    )
    assert [e.reason for e in errors] == [
        "unknown_attribute",
        "invalid_attribute",
        "invalid_attribute",
    ]
    assert [e.path for e in errors] == [
        "attributes.extra",
        "attributes.method",
        "attributes.max_steps",
    ]


def test_explicit_null_attribute_is_invalid() -> None:
    schema = (attrs.Attribute("method", "enum", enum=("sift", "superpoint")),)
    errors = attrs.validate_attributes(schema, {"method": None})
    assert [e.reason for e in errors] == ["invalid_attribute"]
    assert errors[0].path == "attributes.method"


def test_attribute_schema_rejects_malformed_bounds_and_defaults() -> None:
    with pytest.raises(ValueError, match="min/max"):
        attrs.Attribute("enabled", "bool", min=0)

    with pytest.raises(ValueError, match="min must be <= max"):
        attrs.Attribute("steps", "int", min=5, max=1)

    with pytest.raises(ValueError, match="default cannot be null"):
        attrs.Attribute("steps", "int", default=None, has_default=True)

    with pytest.raises(ValueError, match="default must match type"):
        attrs.Attribute("steps", "int", default=True, has_default=True)

    with pytest.raises(ValueError, match="enum values must be unique"):
        attrs.Attribute("method", "enum", enum=("sift", "sift"))

    with pytest.raises(ValueError, match="enum values must be strings"):
        attrs.Attribute("method", "enum", enum=("sift", 3))  # type: ignore[arg-type]


def test_core_contract_does_not_import_plugin() -> None:
    import importlib
    import sys

    before = set(sys.modules)
    importlib.reload(attrs)
    leaked = {m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")}
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
