"""DataType registry — the data objects of the typed pipeline.

The framework is a typed pipeline of operations over data. DataTypes are the
nouns: the logical objects that flow between operations. They are the only
typed axis -- composition is checked by matching DataType ids nominally
(ids match or they don't; bridging requires an explicit conversion).

DataType is the *logical* object, independent of serialization. One type has
many *formats* (a ``feature_set`` may be COLMAP descriptors or an h5 file);
the Format axis lives in :mod:`sceneapi.server.core.artifacts`. Keeping them separate
means composition is format-independent and a cross-format coercion is a
type-preserving execution detail, not a pipeline edge.

The vocabulary itself is owned by the contract plane
(:mod:`sceneapi_io.formats.datatypes`); this module is a behavior-identical
re-export kept at the historical path so every importer (processors, the
dataflow registry, sfm_hub models, tests) is untouched. Operations
(:mod:`sceneapi.server.core.operations`) declare their ``consumes``/``produces`` over
these ids; pipelines (:mod:`sceneapi.server.core.pipelines`) type-check by threading them.
"""

from __future__ import annotations

from sceneapi_io.formats.datatypes import (
    CONTRACT_NAME,
    CONTRACT_SCHEMA_VERSION,
    CORE_DATA_TYPES,
    CORE_DATA_TYPES_BY_ID,
    DATA_TYPE_KINDS,
    DataType,
    contract_dict,
    is_data_type,
)

__all__ = [
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "CORE_DATA_TYPES",
    "CORE_DATA_TYPES_BY_ID",
    "DATA_TYPE_KINDS",
    "DataType",
    "contract_dict",
    "is_data_type",
]
