"""Action signatures — the morphism axis of the typed dataflow.

An action is a typed transform: it ``consumes`` zero or more DataTypes and
``produces`` zero or more DataTypes (see :mod:`app.core.datatypes`). This is
the *data* edge of an action, distinct from its free-form ``input_schema``
parameters -- only the typed edges participate in chain type-checking.

Signatures are declared over *logical* DataType ids, not serialization
formats: ``colmap.feature_extractor`` is ``image_sequence -> feature_set``
regardless of whether features land in a COLMAP DB or an h5 file (that is the
Format axis, resolved by type-preserving coercion at execution).

Signatures are keyed by ``action_id`` (provider-independent): every backend's
``colmap.feature_extractor`` shares this signature. This is a repo-owned core
contract -- a data standard (no plugin import); plugins conform by exposing
actions whose ids carry a declared signature here. ``gen_contracts.py``
serializes :func:`contract_dict` to JSON + a C++ ``.inc``; the chain validator
(:mod:`app.core.pipelines`) reads these signatures to type-check a DAG.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.datatypes import is_data_type


@dataclass(frozen=True)
class ActionSignature:
    action_id: str
    consumes: tuple[str, ...]   # DataType ids on the input edge
    produces: tuple[str, ...]   # DataType ids on the output edge


# Seed: the core SfM pipeline expressed as typed transforms. Declaration order
# is the serialization order so the contract JSON is stable. Utility / dense /
# database / io commands carry no data signature yet and are simply absent.
CORE_ACTION_SIGNATURES: tuple[ActionSignature, ...] = (
    ActionSignature("colmap.feature_extractor", ("image_sequence",), ("feature_set",)),
    ActionSignature("colmap.feature_importer", ("image_sequence",), ("feature_set",)),
    ActionSignature("colmap.exhaustive_matcher", ("feature_set",), ("match_graph",)),
    ActionSignature("colmap.sequential_matcher", ("feature_set",), ("match_graph",)),
    ActionSignature("colmap.spatial_matcher", ("feature_set",), ("match_graph",)),
    ActionSignature("colmap.vocab_tree_matcher", ("feature_set",), ("match_graph",)),
    ActionSignature("colmap.transitive_matcher", ("match_graph",), ("match_graph",)),
    ActionSignature("colmap.mapper", ("match_graph",), ("sparse_reconstruction",)),
    ActionSignature("colmap.hierarchical_mapper", ("match_graph",), ("sparse_reconstruction",)),
    ActionSignature("colmap.point_triangulator",
                    ("sparse_reconstruction", "match_graph"), ("sparse_reconstruction",)),
    ActionSignature("colmap.bundle_adjuster", ("sparse_reconstruction",), ("sparse_reconstruction",)),
    ActionSignature("colmap.image_registrator", ("sparse_reconstruction",), ("sparse_reconstruction",)),
)

SIGNATURES_BY_ID: dict[str, ActionSignature] = {
    s.action_id: s for s in CORE_ACTION_SIGNATURES
}

# Fail fast at import if a seed references an unknown DataType (a typo would
# otherwise silently produce an un-typecheckable signature).
for _s in CORE_ACTION_SIGNATURES:
    for _t in (*_s.consumes, *_s.produces):
        if not is_data_type(_t):
            raise ValueError(
                f"action signature {_s.action_id!r} references unknown DataType {_t!r}"
            )


def signature_for(action_id: str) -> ActionSignature | None:
    return SIGNATURES_BY_ID.get(action_id)


CONTRACT_NAME = "action_signatures"
CONTRACT_SCHEMA_VERSION = 1


def contract_dict() -> dict:
    """The action-signature registry as a deterministic, JSON-serializable dict."""
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "signatures": [
            {
                "action_id": s.action_id,
                "consumes": list(s.consumes),
                "produces": list(s.produces),
            }
            for s in CORE_ACTION_SIGNATURES
        ],
    }


__all__ = [
    "CORE_ACTION_SIGNATURES",
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "ActionSignature",
    "SIGNATURES_BY_ID",
    "contract_dict",
    "signature_for",
]
