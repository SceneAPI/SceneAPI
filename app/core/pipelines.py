"""Pipelines - typed compositions of Processors.

The native pipeline model is a topologically ordered DAG of Processor
instances wired by named ports. Legacy callers may still pass a flat list of
operation ids; that path uses the historical set-availability validator and is
kept as a compatibility view.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.attributes import AttributeSet, validate_attributes
from app.core.processors import PortSpec, Processor, processor_for

# What a pipeline starts with: the captured images.
DEFAULT_INITIAL_INPUTS: tuple[str, ...] = ("image_sequence",)


@dataclass(frozen=True)
class ChainError:
    where: str
    message: str
    reason: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class PipelineStep:
    ref: str
    processor: str
    attributes: dict[str, Any] = field(default_factory=dict)
    wires: dict[str, Any] = field(default_factory=dict)


class ProcessorLookup(Protocol):
    def __call__(self, processor_id: str) -> Processor | None: ...


@dataclass(frozen=True)
class _Supply:
    ref: str
    port: str
    datatype: str
    verified_match_graph: bool = False

    @property
    def wire_ref(self) -> str:
        return f"{self.ref}.{self.port}"


# Canonical pipelines as ordered operation steps. Each must type-check from
# DEFAULT_INITIAL_INPUTS (enforced by the contract test).
CANONICAL_PIPELINES: dict[str, tuple[str, ...]] = {
    "sfm": ("features", "pairs", "matches", "verify", "map"),
}


def _legacy_validate_pipeline(
    op_ids: list[str],
    initial_inputs: tuple[str, ...],
    processor_lookup: ProcessorLookup,
) -> list[ChainError]:
    """Historical flat type-availability validator for /v1/operations clients."""
    available: Counter[str] = Counter(initial_inputs)
    verified_match_graphs = 0
    errors: list[ChainError] = []
    for i, op_id in enumerate(op_ids):
        op = processor_lookup(op_id)
        if op is None:
            errors.append(
                ChainError(
                    f"step {i}",
                    f"unknown operation '{op_id}'",
                    "unknown_processor",
                    f"steps.{i}",
                )
            )
            continue
        missing = [t for t in op.consumes if available[t] == 0]
        if missing:
            errors.append(
                ChainError(
                    f"step {i} '{op_id}'",
                    f"missing input(s): {', '.join(missing)}; "
                    f"upstream produced: {', '.join(sorted(available))}",
                    "missing_required_port",
                    f"steps.{i}",
                )
            )
        for role, port in op.consumer.items():
            if port.multiple and port.required and available[port.datatype] == 1:
                errors.append(
                    ChainError(
                        f"step {i} '{op_id}'",
                        f"consumer port '{role}' requires at least two inputs",
                        "invalid_fan_in",
                        f"steps.{i}",
                    )
                )
            if (
                _requires_verified_match_graph(op_id, role)
                and available[port.datatype] > 0
                and verified_match_graphs == 0
            ):
                errors.append(
                    ChainError(
                        f"step {i} '{op_id}'",
                        f"consumer port '{role}' requires verified match_graph input",
                        "unverified_match_graph",
                        f"steps.{i}",
                    )
                )
        available.update(op.produces)
        if op_id == "verify":
            verified_match_graphs += op.produces.count("match_graph")
    return errors


def _parse_wire_ref(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, str) or "." not in value:
        return None
    if value.startswith("inputs."):
        port = value.removeprefix("inputs.")
        return ("inputs", port) if port else None
    ref, port = value.rsplit(".", 1)
    if not ref or not port:
        return None
    return ref, port


def _wire_values(raw: Any, port: PortSpec) -> list[Any]:
    if port.multiple:
        return raw if isinstance(raw, list) else [raw]
    return [raw]


def _available_datatypes(available: list[_Supply]) -> str:
    return ", ".join(sorted({s.datatype for s in available}))


def _requires_verified_match_graph(processor_id: str, role: str) -> bool:
    return processor_id in {"map", "triangulate"} and role == "matches"


def _processor_consumer(processor: Processor) -> dict[str, PortSpec]:
    return {**processor.consumer, **processor.special_inputs}


def _processor_attributes(processor: Processor) -> AttributeSet:
    return (*processor.attributes, *processor.special_attributes)


def _step_supply(step: PipelineStep, role: str, datatype: str) -> _Supply:
    return _Supply(
        step.ref,
        role,
        datatype,
        verified_match_graph=(
            step.processor == "verify" and role == "matches" and datatype == "match_graph"
        ),
    )


def _strict_validate_pipeline(
    steps: list[PipelineStep],
    initial_inputs: tuple[str, ...],
    processor_lookup: ProcessorLookup,
) -> list[ChainError]:
    available: list[_Supply] = [
        _Supply("inputs", datatype, datatype) for datatype in initial_inputs
    ]
    by_wire = {s.wire_ref: s for s in available}
    seen_refs: set[str] = {"inputs"}
    errors: list[ChainError] = []
    duplicate_initial_inputs = sorted(
        {datatype for datatype in initial_inputs if initial_inputs.count(datatype) > 1}
    )
    for datatype in duplicate_initial_inputs:
        errors.append(
            ChainError(
                "inputs",
                f"duplicate initial input datatype '{datatype}'",
                "duplicate_initial_input",
                "initial_inputs",
            )
        )

    for i, step in enumerate(steps):
        where = f"step {i} '{step.ref}'"
        path = f"steps.{i}"
        if step.ref in seen_refs:
            errors.append(
                ChainError(
                    where,
                    f"duplicate step ref '{step.ref}'",
                    "duplicate_step_ref",
                    f"{path}.ref",
                )
            )
            continue
        seen_refs.add(step.ref)

        processor = processor_lookup(step.processor)
        if processor is None:
            errors.append(
                ChainError(
                    where,
                    f"unknown processor '{step.processor}'",
                    "unknown_processor",
                    f"{path}.processor",
                )
            )
            continue

        consumer = _processor_consumer(processor)
        for attr_error in validate_attributes(
            _processor_attributes(processor),
            step.attributes,
            path_prefix=f"{path}.attributes",
        ):
            errors.append(
                ChainError(
                    where,
                    attr_error.message,
                    attr_error.reason,
                    attr_error.path,
                )
            )

        unknown_wires = sorted(set(step.wires) - set(consumer))
        for role in unknown_wires:
            errors.append(
                ChainError(
                    where,
                    f"unknown consumer port '{role}'",
                    "unknown_port",
                    f"{path}.wires.{role}",
                )
            )

        for role, port in consumer.items():
            if role in step.wires:
                raw_wire = step.wires[role]
                values = _wire_values(raw_wire, port)
                if not port.multiple and isinstance(raw_wire, list):
                    errors.append(
                        ChainError(
                            where,
                            f"consumer port '{role}' does not accept multiple inputs",
                            "invalid_fan_in",
                            f"{path}.wires.{role}",
                        )
                    )
                    continue
                if not values and port.required:
                    errors.append(
                        ChainError(
                            where,
                            f"missing required input port '{role}' ({port.datatype})",
                            "missing_required_port",
                            f"{path}.wires.{role}",
                        )
                    )
                    continue
                if port.multiple and port.required and len(values) == 1:
                    errors.append(
                        ChainError(
                            where,
                            f"consumer port '{role}' requires at least two inputs",
                            "invalid_fan_in",
                            f"{path}.wires.{role}",
                        )
                    )
                    continue
                wire_keys: list[str] = []
                for value in values:
                    parsed = _parse_wire_ref(value)
                    if parsed is None:
                        errors.append(
                            ChainError(
                                where,
                                f"wire for port '{role}' must be 'step_ref.supplier_port'",
                                "unknown_port",
                                f"{path}.wires.{role}",
                            )
                        )
                        continue
                    supply = by_wire.get(f"{parsed[0]}.{parsed[1]}")
                    if supply is None:
                        errors.append(
                            ChainError(
                                where,
                                f"unknown supplier port '{parsed[0]}.{parsed[1]}'",
                                "unknown_port",
                                f"{path}.wires.{role}",
                            )
                        )
                        continue
                    wire_keys.append(supply.wire_ref)
                    if supply.datatype != port.datatype:
                        errors.append(
                            ChainError(
                                where,
                                f"datatype mismatch for port '{role}': expected "
                                f"{port.datatype}, got {supply.datatype}",
                                "datatype_mismatch",
                                f"{path}.wires.{role}",
                            )
                        )
                        continue
                    if (
                        _requires_verified_match_graph(step.processor, role)
                        and not supply.verified_match_graph
                    ):
                        errors.append(
                            ChainError(
                                where,
                                f"consumer port '{role}' requires verified match_graph input",
                                "unverified_match_graph",
                                f"{path}.wires.{role}",
                            )
                        )
                distinct_wire_keys = set(wire_keys)
                if port.multiple and len(distinct_wire_keys) != len(wire_keys):
                    errors.append(
                        ChainError(
                            where,
                            f"consumer port '{role}' does not accept duplicate inputs",
                            "invalid_fan_in",
                            f"{path}.wires.{role}",
                        )
                    )
                elif port.multiple and port.required and len(distinct_wire_keys) < 2:
                    errors.append(
                        ChainError(
                            where,
                            f"consumer port '{role}' requires at least two distinct inputs",
                            "invalid_fan_in",
                            f"{path}.wires.{role}",
                        )
                    )
                continue

            if not port.required:
                continue

            candidates = [s for s in available if s.datatype == port.datatype]
            if not candidates:
                if port.required:
                    errors.append(
                        ChainError(
                            where,
                            f"missing input(s): {port.datatype}; upstream produced: "
                            f"{_available_datatypes(available)}",
                            "missing_required_port",
                            f"{path}.wires.{role}",
                        )
                    )
                continue
            if port.multiple and port.required and len(candidates) == 1:
                errors.append(
                    ChainError(
                        where,
                        f"consumer port '{role}' requires at least two inputs",
                        "invalid_fan_in",
                        f"{path}.wires.{role}",
                    )
                )
                continue
            if port.multiple and port.required and len({s.wire_ref for s in candidates}) < 2:
                errors.append(
                    ChainError(
                        where,
                        f"consumer port '{role}' requires at least two distinct inputs",
                        "invalid_fan_in",
                        f"{path}.wires.{role}",
                    )
                )
                continue
            if not port.multiple and len(candidates) > 1:
                errors.append(
                    ChainError(
                        where,
                        f"ambiguous input for port '{role}' ({port.datatype}): "
                        f"{', '.join(s.wire_ref for s in candidates)}",
                        "ambiguous_input",
                        f"{path}.wires.{role}",
                    )
                )
                continue
            if _requires_verified_match_graph(step.processor, role):
                verified_candidates = [s for s in candidates if s.verified_match_graph]
                if not verified_candidates:
                    errors.append(
                        ChainError(
                            where,
                            f"consumer port '{role}' requires verified match_graph input",
                            "unverified_match_graph",
                            f"{path}.wires.{role}",
                        )
                    )
                    continue

        for role, port in processor.supplier.items():
            supply = _step_supply(step, role, port.datatype)
            available.append(supply)
            by_wire[supply.wire_ref] = supply

    return errors


def validate_pipeline(
    steps: list[str] | list[PipelineStep],
    initial_inputs: tuple[str, ...] = DEFAULT_INITIAL_INPUTS,
    *,
    processor_lookup: ProcessorLookup | None = None,
) -> list[ChainError]:
    """Type-check a legacy operation list or a named-port Processor DAG."""
    lookup = processor_lookup or processor_for
    if not steps:
        return []
    if all(isinstance(s, str) for s in steps):
        return _legacy_validate_pipeline(steps, initial_inputs, lookup)  # type: ignore[arg-type]
    return _strict_validate_pipeline(steps, initial_inputs, lookup)  # type: ignore[arg-type]


def validate_step_attributes(
    steps: list[PipelineStep],
    *,
    processor_lookup: ProcessorLookup | None = None,
) -> list[ChainError]:
    """Validate per-step Processor attributes without checking port wiring.

    This is used by legacy ``op`` pipelines with ``params``: the sequence keeps
    the historical flat availability rule, but bound params still get the
    Processor attribute schema.
    """
    lookup = processor_lookup or processor_for
    errors: list[ChainError] = []
    for i, step in enumerate(steps):
        processor = lookup(step.processor)
        if processor is None:
            continue
        where = f"step {i} '{step.ref}'"
        path = f"steps.{i}"
        for attr_error in validate_attributes(
            _processor_attributes(processor),
            step.attributes,
            path_prefix=f"{path}.attributes",
        ):
            errors.append(
                ChainError(
                    where,
                    attr_error.message,
                    attr_error.reason,
                    attr_error.path,
                )
            )
    return errors


def inferred_step_dependencies(
    steps: list[PipelineStep],
    initial_inputs: tuple[str, ...] = DEFAULT_INITIAL_INPUTS,
    *,
    processor_lookup: ProcessorLookup | None = None,
) -> dict[str, list[str]]:
    """Resolve the task-level step dependencies implied by port wiring.

    Call after ``validate_pipeline(steps)`` succeeds. Dependencies on the
    synthetic ``inputs`` source are omitted because no task produces them.
    """
    lookup = processor_lookup or processor_for
    available: list[_Supply] = [
        _Supply("inputs", datatype, datatype) for datatype in initial_inputs
    ]
    by_wire = {s.wire_ref: s for s in available}
    out: dict[str, list[str]] = {}

    def add_dep(deps: list[str], ref: str) -> None:
        if ref != "inputs" and ref not in deps:
            deps.append(ref)

    for step in steps:
        processor = lookup(step.processor)
        if processor is None:
            out[step.ref] = []
            continue

        deps: list[str] = []
        for role, port in _processor_consumer(processor).items():
            if role in step.wires:
                values = _wire_values(step.wires[role], port)
                for value in values:
                    parsed = _parse_wire_ref(value)
                    if parsed is None:
                        continue
                    supply = by_wire.get(f"{parsed[0]}.{parsed[1]}")
                    if supply is not None:
                        add_dep(deps, supply.ref)
                continue

            if not port.required:
                continue

            for supply in available:
                if supply.datatype == port.datatype and (
                    not _requires_verified_match_graph(step.processor, role)
                    or supply.verified_match_graph
                ):
                    add_dep(deps, supply.ref)
                    if not port.multiple:
                        break

        out[step.ref] = deps
        for role, port in processor.supplier.items():
            supply = _step_supply(step, role, port.datatype)
            available.append(supply)
            by_wire[supply.wire_ref] = supply
    return out


CONTRACT_NAME = "pipelines"
CONTRACT_SCHEMA_VERSION = 1

_COMPOSITION_RULE = (
    "A pipeline is valid iff each Processor consumer port is wired from an "
    "upstream supplier port with the same DataType. Nominal: ids match exactly; "
    "a missing input requires an explicit conversion Processor, never an "
    "implicit type coercion. Compatibility refinement: map/triangulate require "
    "match_graph supplied by verify until raw/verified match graphs are split "
    "into distinct DataTypes. Legacy operation lists retain the flat "
    "availability projection for compatibility."
)


def contract_dict() -> dict[str, Any]:
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "composition_rule": _COMPOSITION_RULE,
        "initial_inputs": list(DEFAULT_INITIAL_INPUTS),
        "canonical_pipelines": {
            name: list(steps) for name, steps in sorted(CANONICAL_PIPELINES.items())
        },
        "plugin_pipelines": [],
        "step_schema": {
            "legacy_step": {
                "string": "legacy operation id for validation only",
                "op": "legacy operation id",
                "params": (
                    "legacy alias for processor attributes; still validated "
                    "against the Processor attribute schema"
                ),
            },
            "ref": "pipeline-local step id",
            "processor": "processor_id",
            "attributes": ("attribute values bound for this step; wins over params on key overlap"),
            "params": "legacy alias for attributes",
            "wires": {
                "consumer_port": (
                    "producer_ref.supplier_port or list of refs for multiple=true ports"
                ),
            },
        },
        "validation_reasons": [
            "unknown_processor",
            "unknown_port",
            "unknown_attribute",
            "datatype_mismatch",
            "ambiguous_input",
            "missing_required_port",
            "missing_required_attribute",
            "invalid_attribute",
            "duplicate_initial_input",
            "unknown_datatype",
            "duplicate_step_ref",
            "invalid_fan_in",
            "unverified_match_graph",
            "provider_unsupported",
        ],
    }


__all__ = [
    "CANONICAL_PIPELINES",
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "DEFAULT_INITIAL_INPUTS",
    "ChainError",
    "PipelineStep",
    "contract_dict",
    "inferred_step_dependencies",
    "validate_pipeline",
    "validate_step_attributes",
]
