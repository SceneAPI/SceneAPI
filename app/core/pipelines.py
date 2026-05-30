"""Pipelines — the composition axis of the typed dataflow.

A pipeline is a DAG of action nodes connected by typed data edges. It is
*valid* iff every edge type-checks **nominally**: the DataType flowing on an
edge must be produced by the source action and consumed by the target action
(:mod:`app.core.action_signatures`). There is no implicit conversion -- if a
producer's output type does not match a consumer's input type, the author must
insert an explicit conversion action (a node whose signature bridges the two).
That keeps composition deterministic and trivially mirrored in the C++ port
(set intersection over embedded signatures -- no JSON-Schema engine).

Linear chains are the common case; :func:`validate_chain` handles full DAGs
(an action may consume several typed inputs, e.g. point_triangulator over
``sparse_reconstruction`` + ``match_graph``).

This is a repo-owned core contract: ``gen_contracts.py`` serializes
:func:`contract_dict` (the composition rule + the canonical pipelines) to JSON
+ a C++ ``.inc``; the C++ port embeds the same canonical pipelines and runs the
same nominal check.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.action_signatures import signature_for
from app.core.datatypes import is_data_type


@dataclass(frozen=True)
class ChainError:
    where: str       # node id / step index pair, human-readable
    message: str


# Canonical SfM pipelines, as ordered action_id steps. Each must type-compose
# end to end (enforced by the contract test). Plugins/users compose their own;
# these are the blessed templates the C++ port also embeds.
CANONICAL_PIPELINES: dict[str, tuple[str, ...]] = {
    "sfm_exhaustive": (
        "colmap.feature_extractor",
        "colmap.exhaustive_matcher",
        "colmap.mapper",
        "colmap.bundle_adjuster",
    ),
    "sfm_sequential": (
        "colmap.feature_extractor",
        "colmap.sequential_matcher",
        "colmap.mapper",
    ),
    "sfm_vocab_tree": (
        "colmap.feature_extractor",
        "colmap.vocab_tree_matcher",
        "colmap.mapper",
        "colmap.bundle_adjuster",
    ),
}


def validate_linear(action_ids: list[str]) -> list[ChainError]:
    """Type-check a linear chain: each step's output must satisfy the next
    step's input. Unsignatured actions and type breaks are reported."""
    errors: list[ChainError] = []
    sigs = [(a, signature_for(a)) for a in action_ids]
    for i, (action_id, s) in enumerate(sigs):
        if s is None:
            errors.append(ChainError(f"step {i}", f"{action_id!r} has no declared signature"))
    for i in range(len(sigs) - 1):
        (a, sa), (b, sb) = sigs[i], sigs[i + 1]
        if sa is None or sb is None:
            continue
        if not (set(sa.produces) & set(sb.consumes)):
            errors.append(ChainError(
                f"step {i}->{i + 1}",
                f"{a!r} produces {list(sa.produces)} but {b!r} consumes "
                f"{list(sb.consumes)}: no shared type (insert a conversion)",
            ))
    return errors


def validate_chain(
    nodes: list[dict],
    edges: list[dict],
) -> list[ChainError]:
    """Type-check a DAG. ``nodes`` are ``{node_id, action_id}``; ``edges`` are
    ``{src, dst, type_id}``. Every edge's DataType must be produced by its
    source action and consumed by its target action."""
    by_id = {str(n["node_id"]): n for n in nodes}
    errors: list[ChainError] = []
    for e in edges:
        src, dst, tid = str(e["src"]), str(e["dst"]), str(e["type_id"])
        where = f"{src}->{dst}"
        if src not in by_id:
            errors.append(ChainError(where, f"edge source {src!r} is not a node"))
            continue
        if dst not in by_id:
            errors.append(ChainError(where, f"edge target {dst!r} is not a node"))
            continue
        if not is_data_type(tid):
            errors.append(ChainError(where, f"edge type {tid!r} is not a known DataType"))
            continue
        src_sig = signature_for(str(by_id[src]["action_id"]))
        dst_sig = signature_for(str(by_id[dst]["action_id"]))
        if src_sig is None or tid not in src_sig.produces:
            errors.append(ChainError(
                where, f"{by_id[src]['action_id']!r} does not produce {tid!r}"))
            continue
        if dst_sig is None or tid not in dst_sig.consumes:
            errors.append(ChainError(
                where, f"{by_id[dst]['action_id']!r} does not consume {tid!r}"))
    return errors


CONTRACT_NAME = "pipelines"
CONTRACT_SCHEMA_VERSION = 1

_COMPOSITION_RULE = (
    "An edge src->dst is valid iff edge.type_id is in produces(src) and in "
    "consumes(dst). Nominal: type ids match exactly; bridging types requires an "
    "explicit conversion action, never an implicit coercion."
)


def contract_dict() -> dict:
    """The composition rule + canonical pipelines, deterministic + serializable."""
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "composition_rule": _COMPOSITION_RULE,
        "canonical_pipelines": {
            name: list(steps) for name, steps in sorted(CANONICAL_PIPELINES.items())
        },
    }


__all__ = [
    "CANONICAL_PIPELINES",
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "ChainError",
    "contract_dict",
    "validate_chain",
    "validate_linear",
]
