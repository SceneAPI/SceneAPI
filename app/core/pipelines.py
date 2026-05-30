"""Pipelines — typed compositions of operations.

A pipeline is an ordered list of operations (:mod:`app.core.operations`). It
is *valid* iff every operation's consumed DataTypes are available when it runs
-- i.e. produced by an upstream operation (or supplied as an initial input).
This threads types through the pipeline and correctly handles multi-input
operations (``map`` needs ``feature_set`` from stage 1 and ``match_graph``
from stage 4, not just its immediate predecessor) while keeping the intuitive
linear-stage view.

Nominal: types match by id; bridging a missing input requires an explicit
conversion operation, never an implicit coercion. The rule is pure
set-availability over the embedded operation signatures -- deterministic and
trivially mirrored in the C++ port.

Repo-owned core contract: ``gen_contracts.py`` serializes :func:`contract_dict`
(the rule + the canonical pipelines) to JSON + a C++ ``.inc``; both tiers run
the same check.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.operations import operation_for

# What a pipeline starts with: the captured images.
DEFAULT_INITIAL_INPUTS: tuple[str, ...] = ("image_sequence",)


@dataclass(frozen=True)
class ChainError:
    where: str       # "step {i}" / "step {i} '{op}'"
    message: str


# Canonical pipelines as ordered operation steps. Each must type-check from
# DEFAULT_INITIAL_INPUTS (enforced by the contract test). The mapping
# algorithm (incremental/global/...) is a parameter of `map`, not a separate
# pipeline -- so the structural pipeline is one, with optional dense/splat tails.
CANONICAL_PIPELINES: dict[str, tuple[str, ...]] = {
    "sfm": ("features", "pairs", "matches", "verify", "map"),
    "sfm_dense": ("features", "pairs", "matches", "verify", "map", "dense"),
    "splat": ("features", "pairs", "matches", "verify", "map", "splat"),
}


def validate_pipeline(
    op_ids: list[str],
    initial_inputs: tuple[str, ...] = DEFAULT_INITIAL_INPUTS,
) -> list[ChainError]:
    """Type-check a pipeline: each operation's consumed types must be available
    (produced upstream or an initial input). Returns per-step errors; empty ==
    valid."""
    available: set[str] = set(initial_inputs)
    errors: list[ChainError] = []
    for i, op_id in enumerate(op_ids):
        op = operation_for(op_id)
        if op is None:
            errors.append(ChainError(f"step {i}", f"unknown operation '{op_id}'"))
            continue
        missing = [t for t in op.consumes if t not in available]
        if missing:
            errors.append(ChainError(
                f"step {i} '{op_id}'",
                f"missing input(s): {', '.join(missing)}; "
                f"upstream produced: {', '.join(sorted(available))}",
            ))
        available.update(op.produces)
    return errors


CONTRACT_NAME = "pipelines"
CONTRACT_SCHEMA_VERSION = 1

_COMPOSITION_RULE = (
    "A pipeline is valid iff each operation's consumed DataTypes are available "
    "when it runs -- produced by an upstream operation or an initial input. "
    "Nominal: ids match exactly; a missing input requires an explicit conversion "
    "operation, never an implicit coercion."
)


def contract_dict() -> dict:
    """The composition rule + canonical pipelines, deterministic + serializable."""
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "composition_rule": _COMPOSITION_RULE,
        "initial_inputs": list(DEFAULT_INITIAL_INPUTS),
        "canonical_pipelines": {
            name: list(steps) for name, steps in sorted(CANONICAL_PIPELINES.items())
        },
    }


__all__ = [
    "CANONICAL_PIPELINES",
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "DEFAULT_INITIAL_INPUTS",
    "ChainError",
    "contract_dict",
    "validate_pipeline",
]
