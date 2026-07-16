"""Compatibility projection for the typed Processor registry.

The native model is :mod:`app.core.processors`: named consumer/supplier ports
plus typed attributes. This module preserves the legacy flat Operation contract
(``op_id``, ``consumes``, ``produces``) for existing clients and tests while the
wire surface migrates to Processors.
"""

from __future__ import annotations

from typing import Any

from app.core.processors import (
    CORE_PROCESSORS,
    PROCESSORS_BY_ID,
    Processor,
    processor_for,
    processor_for_capability,
    processors_for_capabilities,
)

Operation = Processor
CORE_OPERATIONS = CORE_PROCESSORS
OPERATIONS_BY_ID = PROCESSORS_BY_ID

CONTRACT_NAME = "operations"
CONTRACT_SCHEMA_VERSION = 1


def operation_for(op_id: str) -> Operation | None:
    return processor_for(op_id)


def operation_for_capability(capability: str) -> str | None:
    return processor_for_capability(capability)


def operations_for_capabilities(capabilities: object) -> set[str]:
    return processors_for_capabilities(capabilities)


def contract_dict() -> dict[str, Any]:
    """The legacy Operation registry as a deterministic flat projection."""
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "operations": [op.operation_contract_dict() for op in CORE_OPERATIONS],
        "compatibility": {
            "native_contract": "processors",
            "status": "legacy_projection",
        },
    }


__all__ = [
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "CORE_OPERATIONS",
    "OPERATIONS_BY_ID",
    "Operation",
    "contract_dict",
    "operation_for",
    "operation_for_capability",
    "operations_for_capabilities",
]
