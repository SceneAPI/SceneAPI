"""Attribute schemas for typed Processors.

Attributes are the portable configuration contract for a Processor: stable
algorithm selectors and common knobs. Provider-specific configuration still
lives in backend config schemas; this module defines the cross-provider shape
that can be validated before a step is submitted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.datatypes import is_data_type

ATTRIBUTE_TYPES = ("int", "float", "bool", "str", "enum", "datatype-ref", "object")
CONTRACT_NAME = "attributes"
CONTRACT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Attribute:
    name: str
    type: str
    required: bool = False
    default: Any = None
    has_default: bool = False
    enum: tuple[str, ...] = ()
    min: int | float | None = None
    max: int | float | None = None
    description: str = ""
    datatype_lookup: Callable[[str], bool] | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.type not in ATTRIBUTE_TYPES:
            raise ValueError(f"attribute {self.name!r} has unknown type {self.type!r}")
        if self.type == "enum" and not self.enum:
            raise ValueError(f"enum attribute {self.name!r} must declare enum values")
        if self.type != "enum" and self.enum:
            raise ValueError(f"attribute {self.name!r} uses enum values but type is {self.type!r}")
        if self.enum and not all(isinstance(value, str) for value in self.enum):
            raise ValueError(f"attribute {self.name!r} enum values must be strings")
        if len(set(map(repr, self.enum))) != len(self.enum):
            raise ValueError(f"attribute {self.name!r} enum values must be unique")
        if self.type not in {"int", "float"} and (self.min is not None or self.max is not None):
            raise ValueError(f"attribute {self.name!r} min/max are only valid for numeric types")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"attribute {self.name!r} min must be <= max")
        if self.required and self.has_default:
            raise ValueError(f"attribute {self.name!r} cannot be required and defaulted")
        if self.has_default:
            if self.default is None:
                raise ValueError(f"attribute {self.name!r} default cannot be null")
            if not _type_ok(self, self.default, datatype_lookup=self.datatype_lookup):
                raise ValueError(f"attribute {self.name!r} default must match type {self.type!r}")
            if (
                self.type in {"int", "float"}
                and isinstance(self.default, (int, float))
                and not isinstance(self.default, bool)
            ):
                if self.min is not None and self.default < self.min:
                    raise ValueError(f"attribute {self.name!r} default must be >= min")
                if self.max is not None and self.default > self.max:
                    raise ValueError(f"attribute {self.name!r} default must be <= max")

    def contract_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }
        if self.has_default:
            out["default"] = self.default
        if self.enum:
            out["enum"] = list(self.enum)
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        return out


AttributeSet = tuple[Attribute, ...]


@dataclass(frozen=True)
class AttributeValidationError:
    reason: str
    path: str
    message: str


def _type_ok(
    attr: Attribute,
    value: Any,
    *,
    datatype_lookup: Callable[[str], bool] | None = None,
) -> bool:
    if attr.type == "bool":
        return isinstance(value, bool)
    if attr.type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if attr.type == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if attr.type == "str":
        return isinstance(value, str)
    if attr.type == "enum":
        return value in attr.enum
    if attr.type == "datatype-ref":
        lookup = datatype_lookup or attr.datatype_lookup or is_data_type
        return isinstance(value, str) and lookup(value)
    if attr.type == "object":
        return isinstance(value, dict)
    return False


def validate_attributes(
    attributes: AttributeSet,
    values: dict[str, Any],
    *,
    path_prefix: str = "attributes",
    datatype_lookup: Callable[[str], bool] | None = None,
) -> list[AttributeValidationError]:
    """Validate bound attribute values against an AttributeSet."""
    by_name = {a.name: a for a in attributes}
    errors: list[AttributeValidationError] = []

    for name in sorted(set(values) - set(by_name)):
        errors.append(
            AttributeValidationError(
                "unknown_attribute",
                f"{path_prefix}.{name}",
                f"unknown attribute '{name}'",
            )
        )
    for attr in attributes:
        if attr.name not in values:
            if attr.required:
                errors.append(
                    AttributeValidationError(
                        "missing_required_attribute",
                        f"{path_prefix}.{attr.name}",
                        f"missing required attribute '{attr.name}'",
                    )
                )
            continue

        value = values[attr.name]
        if value is None:
            errors.append(
                AttributeValidationError(
                    "invalid_attribute",
                    f"{path_prefix}.{attr.name}",
                    f"attribute '{attr.name}' must be {attr.type}",
                )
            )
            continue

        if not _type_ok(attr, value, datatype_lookup=datatype_lookup):
            errors.append(
                AttributeValidationError(
                    "invalid_attribute",
                    f"{path_prefix}.{attr.name}",
                    f"attribute '{attr.name}' must be {attr.type}",
                )
            )
            continue
        if attr.min is not None and value < attr.min:
            errors.append(
                AttributeValidationError(
                    "invalid_attribute",
                    f"{path_prefix}.{attr.name}",
                    f"attribute '{attr.name}' must be >= {attr.min}",
                )
            )
        if attr.max is not None and value > attr.max:
            errors.append(
                AttributeValidationError(
                    "invalid_attribute",
                    f"{path_prefix}.{attr.name}",
                    f"attribute '{attr.name}' must be <= {attr.max}",
                )
            )
    return errors


def contract_dict() -> dict[str, Any]:
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "attribute_types": list(ATTRIBUTE_TYPES),
        "rules": {
            "required_with_default": "invalid",
            "unknown_core_attribute": "unknown_attribute",
            "plugin_attribute_namespace": "plugin_id.attribute_name",
        },
    }


__all__ = [
    "ATTRIBUTE_TYPES",
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "Attribute",
    "AttributeSet",
    "AttributeValidationError",
    "contract_dict",
    "validate_attributes",
]
