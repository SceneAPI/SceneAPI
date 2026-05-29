"""COLMAP command/action surface — the sfmapi core action standard.

Companion to :mod:`app.core.colmap_db`. That module owns the COLMAP
*data* format (the scene database); this one owns the COLMAP *command*
surface: the ``action_id`` namespace COLMAP-family backends expose, the
closed set of command categories, the read-only / GPU classification,
and the input-schema *kind* that drives validation.

Ownership: owned here. sfmapi defines the standard; the ``sfmapi_colmap``
plugin (and any COLMAP-family backend) conforms. Like ``colmap_db`` this
is a *data standard*, not a dependency — core declares it as plain
constants and never imports the plugin or links the COLMAP binary. The
per-command native option schema is *runtime* data the installed backend
reports (``colmap_command_schema``); the standard defines the stable
vocabulary and the CLI input-schema shape, not the flags a given build
happens to have.

This is the declared off-wire contract behind the COLMAP action layer:
``tools/gen_contracts.py`` serializes :func:`contract_dict` to JSON + a
C++ ``.inc`` and check_sync's ``contract-parity`` gate pins the embedded
copy byte-identical to this source of truth.

Why a standard, not a plugin special-case: the generic action adapter
(:mod:`app.adapters.backend_actions`) dispatches on the schema *kind*
declared here, not on a hardcoded ``startswith("colmap.")``. COLMAP's
specialness is "the one action standard whose vocabulary ships in core",
expressed through the same contract mechanism as every other.
"""

from __future__ import annotations

# --- action namespace + validation kind -----------------------------------

# action_id prefix for every COLMAP command action
# (e.g. "colmap.feature_extractor"). Declared here so the generic action
# adapter keys off the schema KIND below rather than this literal string.
ACTION_NAMESPACE = "colmap"

# How a COLMAP action's inputs are validated: "cli" == named options +
# positional args (the CLI input-schema kind), as opposed to a plain
# "json" object schema. The generic validator dispatches on this kind,
# not on the backend name.
INPUT_SCHEMA_KIND = "cli"

# --- command classification (the closed vocabulary) ------------------------

# Commands with no write side effects: idempotent, not long-running,
# served with side_effects="read".
READ_ONLY_COMMANDS = frozenset(
    {"help", "version", "model_analyzer", "model_comparer"}
)

# Commands that do not require a GPU: the read-only set plus the CPU-only
# database-maintenance command.
GPU_EXEMPT_COMMANDS = READ_ONLY_COMMANDS | {"database_cleaner"}

# The closed set of categories :func:`category_for` may return. Pinned by
# the contract so the served "category" field stays a known vocabulary.
CATEGORIES = frozenset(
    {"matching", "features", "mapping", "model", "dense", "database", "utility"}
)


def category_for(command: str) -> str:
    """Classify a COLMAP command into exactly one :data:`CATEGORIES` member."""
    if "matcher" in command or "verifier" in command:
        return "matching"
    if command in {"feature_extractor", "feature_importer"}:
        return "features"
    if "mapper" in command or command in {"point_triangulator", "bundle_adjuster"}:
        return "mapping"
    if command.startswith("model_") or command in {"image_registrator", "image_deleter"}:
        return "model"
    if command in {"patch_match_stereo", "stereo_fusion", "poisson_mesher", "delaunay_mesher"}:
        return "dense"
    if command.startswith("database_"):
        return "database"
    return "utility"


def is_read_only(command: str) -> bool:
    """Whether ``command`` has no write side effects."""
    return command in READ_ONLY_COMMANDS


def requires_gpu(command: str) -> bool:
    """Whether ``command`` needs a GPU (everything but the exempt set)."""
    return command not in GPU_EXEMPT_COMMANDS


# --- declared contract -----------------------------------------------------

CONTRACT_NAME = "colmap_actions"
CONTRACT_SCHEMA_VERSION = 1  # version of THIS serialization shape


def contract_dict() -> dict:
    """The COLMAP action standard as a deterministic, JSON-serializable dict.

    ``tools/gen_contracts.py`` serializes this to JSON + a C++ ``.inc``;
    check_sync's ``contract-parity`` gate keeps the embedded copy
    byte-identical. Ordering is stable (sorted) so the JSON is reproducible.
    """
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "action_namespace": ACTION_NAMESPACE,
        "input_schema_kind": INPUT_SCHEMA_KIND,
        "categories": sorted(CATEGORIES),
        "read_only_commands": sorted(READ_ONLY_COMMANDS),
        "gpu_exempt_commands": sorted(GPU_EXEMPT_COMMANDS),
    }


__all__ = [
    "ACTION_NAMESPACE",
    "CATEGORIES",
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "GPU_EXEMPT_COMMANDS",
    "INPUT_SCHEMA_KIND",
    "READ_ONLY_COMMANDS",
    "category_for",
    "contract_dict",
    "is_read_only",
    "requires_gpu",
]
