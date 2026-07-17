"""COLMAP scene-database schema — the sfmapi core data-format contract.

sfmapi treats the COLMAP SQLite scene database as a first-class core
*contract*: the canonical on-disk representation that COLMAP-family
backends produce and that portable tooling (the C++ port, importers,
exporters, the bridge) reads. This module is the single source of truth
for that schema in the framework.

Ownership: this contract is **owned here**. sfmapi defines the standard;
implementations conform to it, not the reverse. The reference COLMAP
fork (``Opsiclear-internal/colmap_mod``) is one such implementation and
is expected to match this schema — if the two ever diverge, this module
is authoritative and the divergence is a bug to reconcile, not a signal
to re-sync from the fork. Changes here are deliberate contract decisions.

This is also a *data standard*, not a dependency. Core declares the
schema as plain data; it never imports the ``sfmapi_colmap`` plugin or
links the COLMAP C++ library. (The
``test_core_does_not_import_plugin_distributions`` guard enforces the
direction.)

The contract defines an **extended** COLMAP schema — a superset of
vanilla upstream COLMAP. Tables/columns absent from upstream are marked
``extension=True`` so consumers can tell the portable core from the
extended surface. The extended surface:

* ``images.time_id`` — per-image 4D / multi-time-frame capture tag
* ``videos`` + ``video_frames`` — video ingestion + frame mapping
* ``image_qualities`` — per-image blur/sharpness
* ``markers`` + ``marker_projections`` — GCPs / named 3D points
* ``descriptors.type`` — extractor type, blocks cross-extractor matching

4D support: ``images.time_id`` is the canonical per-image capture-time
store (every image read populates ``Image.time_id`` from it);
``video_frames.time_id`` is the video-source-specific echo of the same
value. The column is nullable and ignored by non-4D readers, so the
``images`` table stays backward-compatible with vanilla upstream COLMAP
(NULL ``time_id`` == static SfM).

Provenance: the initial values here were established by reading the
reference implementation (``colmap_mod`` ``src/colmap/scene/database_sqlite.cc``
+ ``src/colmap/util/{version,types}.h`` at commit ``8f8e4dd92``,
COLMAP 3.14.0.dev0, database schema revision 2). That is provenance, not
a sync source: evolving the contract is a deliberate edit to this module
(bump ``DATABASE_SCHEMA_REVISION``, update the tables/registry), after
which the reference implementation is expected to conform. The contract
test pins the version + extension surface so unintended drift is caught.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- schema version -------------------------------------------------------
#
# colmap_mod versions the DB via ``PRAGMA user_version`` using a 4-part
# number: major*10^6 + minor*10^4 + patch*100 + revision. The 4th
# (revision) component is a fork extension so schema migrations can ship
# within a single COLMAP release.

DATABASE_VERSION_MAJOR = 3
DATABASE_VERSION_MINOR = 14
DATABASE_VERSION_PATCH = 0
DATABASE_SCHEMA_REVISION = 2


def make_database_version_number(major: int, minor: int, patch: int, revision: int) -> int:
    """The contract's DB version-number encoding. The reference
    implementation matches this in ``util/version.cc``
    (``MakeDatabaseVersionNumber``)."""
    if not (minor < 100 and patch < 100 and revision < 100):
        raise ValueError("minor/patch/revision components must each be < 100")
    return major * 1_000_000 + minor * 10_000 + patch * 100 + revision


DATABASE_VERSION_NUMBER = make_database_version_number(
    DATABASE_VERSION_MAJOR,
    DATABASE_VERSION_MINOR,
    DATABASE_VERSION_PATCH,
    DATABASE_SCHEMA_REVISION,
)

# --- feature extractor / matcher type registry ---------------------------
#
# ``descriptors.type`` records WHICH extractor produced a descriptor set so
# the matcher can refuse to join incompatible descriptor types. colmap_mod
# backs this with a *closed* C++ enum (``FeatureExtractorType``), but the
# sfmapi contract treats extractor identity as an **open registry**:
#
#   * The invariant is the GUARD, not a fixed enum -- "a match may only
#     join two descriptor sets of the same extractor type." Arbitrary
#     extractor ids are permitted; the contract does not cap the set.
#   * ``descriptors.type`` (INTEGER) physically stores a colmap_mod
#     ``FeatureExtractorType`` value, so a DB written by colmap_mod
#     round-trips. The known seed mapping is below.
#   * An extractor OUTSIDE the colmap_mod enum has two contract-legal
#     homes: (a) extend the colmap_mod enum (a fork C++ change) to store
#     it in ``descriptors.type``, or (b) emit portable
#     ``matches.coordinates.v1`` / ``matches.dense.v1`` artifacts, which
#     carry image-coordinate matches directly and never touch the COLMAP
#     keypoint/descriptor tables -- the standard route for detector-free
#     / dense models (LoFTR, RoMa, MASt3R) that have no descriptors at all.
#
# So "arbitrary matching model" is supported: detector-based extractors
# beyond the seed extend the registry (and, for COLMAP-native storage, the
# fork enum); detector-free models bypass descriptor typing via the
# coordinate/dense match formats.

#: Extractor-type ids known to the reference fork, mapped to their
#: colmap_mod ``FeatureExtractorType`` integer values (the on-disk
#: ``descriptors.type`` encoding). ``UNDEFINED`` is -1.
COLMAP_KNOWN_EXTRACTOR_TYPES: dict[str, int] = {
    "SIFT": 0,
    "ALIKED_N16ROT": 1,
    "ALIKED_N32": 2,
}
UNDEFINED_EXTRACTOR_TYPE = -1

#: Matcher-type ids known to the reference fork (colmap_mod
#: ``FeatureMatcherType``). Like extractors, the registry is open --
#: this is the known seed, not a cap.
COLMAP_KNOWN_MATCHER_TYPES: tuple[str, ...] = (
    "SIFT_BRUTEFORCE",
    "SIFT_LIGHTGLUE",
    "ALIKED_BRUTEFORCE",
    "ALIKED_LIGHTGLUE",
)


def is_colmap_native_extractor_type(name: str) -> bool:
    """Whether ``name`` is storable as a colmap_mod ``descriptors.type``
    integer today. A False result does NOT mean the extractor is invalid
    -- it means it must either extend the fork enum or route through the
    portable coordinate/dense match formats (see module docstring)."""
    return name in COLMAP_KNOWN_EXTRACTOR_TYPES


def matches_are_type_compatible(type_a: str, type_b: str) -> bool:
    """The cross-extractor matching guard: a match may only join two
    descriptor sets produced by the same extractor type. This is the
    contract invariant -- it holds for arbitrary extractor ids, not just
    the colmap_mod seed."""
    return type_a == type_b


# --- pair_id encoding -----------------------------------------------------
#
# matches / two_view_geometries are keyed by a single ``pair_id`` derived
# from the two image ids. The contract: the cap is INT32_MAX, and the pair
# id places the *smaller* image id in the high digits (the reference
# implementation matches this in ``util/types.h``). Encoding is part of
# the standard — any tool reading the matches tables must decode pair_id
# the same way.

MAX_NUM_IMAGES = 2_147_483_647  # std::numeric_limits<int32_t>::max()


def image_pair_to_pair_id(image_id1: int, image_id2: int) -> int:
    """COLMAP pair_id from an unordered image-id pair."""
    if image_id1 < 0 or image_id2 < 0:
        raise ValueError("image ids must be non-negative")
    if image_id1 >= MAX_NUM_IMAGES or image_id2 >= MAX_NUM_IMAGES:
        raise ValueError("image id exceeds MAX_NUM_IMAGES")
    if image_id1 > image_id2:
        return MAX_NUM_IMAGES * image_id2 + image_id1
    return MAX_NUM_IMAGES * image_id1 + image_id2


def pair_id_to_image_pair(pair_id: int) -> tuple[int, int]:
    """Inverse of :func:`image_pair_to_pair_id`."""
    image_id2 = pair_id % MAX_NUM_IMAGES
    image_id1 = (pair_id - image_id2) // MAX_NUM_IMAGES
    return image_id1, image_id2


# --- table / column model -------------------------------------------------


@dataclass(frozen=True)
class ColumnDef:
    name: str
    sql_type: str  # INTEGER / TEXT / REAL / BLOB
    #: True when the column does not exist in vanilla upstream COLMAP.
    extension: bool = False
    note: str = ""


@dataclass(frozen=True)
class TableDef:
    name: str
    columns: tuple[ColumnDef, ...]
    #: True when the whole table is absent from vanilla upstream COLMAP.
    extension: bool = False
    note: str = ""

    def column(self, name: str) -> ColumnDef | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def extension_columns(self) -> tuple[ColumnDef, ...]:
        return tuple(c for c in self.columns if c.extension)


def _col(name: str, sql_type: str, **kw: object) -> ColumnDef:
    return ColumnDef(name=name, sql_type=sql_type, **kw)  # type: ignore[arg-type]


# --- the schema -----------------------------------------------------------
#
# Ordering matches CreateTables() in colmap_mod database_sqlite.cc.

COLMAP_DB_TABLES: tuple[TableDef, ...] = (
    # ---- upstream-standard (COLMAP 3.10+ rig/frame/sensor model) ----
    TableDef(
        "rigs",
        (
            _col("rig_id", "INTEGER"),
            _col("ref_sensor_id", "INTEGER"),
            _col("ref_sensor_type", "INTEGER"),
        ),
    ),
    TableDef(
        "rig_sensors",
        (
            _col("rig_id", "INTEGER"),
            _col("sensor_id", "INTEGER"),
            _col("sensor_type", "INTEGER"),
            _col("sensor_from_rig", "BLOB"),
        ),
    ),
    TableDef(
        "cameras",
        (
            _col("camera_id", "INTEGER"),
            _col("model", "INTEGER"),
            _col("width", "INTEGER"),
            _col("height", "INTEGER"),
            _col("params", "BLOB"),
            _col("prior_focal_length", "INTEGER"),
        ),
    ),
    TableDef(
        "frames",
        (
            _col("frame_id", "INTEGER"),
            _col("rig_id", "INTEGER"),
        ),
    ),
    TableDef(
        "frame_data",
        (
            _col("frame_id", "INTEGER"),
            _col("data_id", "INTEGER"),
            _col("sensor_id", "INTEGER"),
            _col("sensor_type", "INTEGER"),
        ),
    ),
    TableDef(
        "images",
        (
            _col("image_id", "INTEGER"),
            _col("name", "TEXT"),
            _col("camera_id", "INTEGER"),
            _col(
                "time_id",
                "INTEGER",
                extension=True,
                note="4D / multi-time-frame tag — the canonical per-image "
                "capture-time store. Read into Image.time_id for every "
                "image (photos, rig captures, and video frames alike); "
                "video_frames.time_id is the video-source-specific echo. "
                "NULL = untagged (static SfM). Nullable + ignored on "
                "SELECT * by non-4D tools, so it stays backward-compatible "
                "with vanilla upstream COLMAP readers.",
            ),
        ),
    ),
    TableDef(
        "pose_priors",
        (
            _col("pose_prior_id", "INTEGER"),
            _col("corr_data_id", "INTEGER"),
            _col("corr_sensor_id", "INTEGER"),
            _col("corr_sensor_type", "INTEGER"),
            _col("position", "BLOB"),
            _col("position_covariance", "BLOB"),
            _col("gravity", "BLOB"),
            _col("coordinate_system", "INTEGER"),
            _col("rotation", "BLOB"),
            _col("rotation_covariance", "BLOB"),
            _col("pose_covariance", "BLOB"),
        ),
    ),
    TableDef(
        "keypoints",
        (
            _col("image_id", "INTEGER"),
            _col("rows", "INTEGER"),
            _col("cols", "INTEGER"),
            _col("data", "BLOB"),
        ),
    ),
    TableDef(
        "descriptors",
        (
            _col("image_id", "INTEGER"),
            _col(
                "type",
                "INTEGER",
                extension=True,
                note="Extractor type (SIFT/ALIKED/...) so cross-extractor "
                "matching is rejected. Added at schema revision 1 "
                "(default SIFT for migrated rows).",
            ),
            _col("rows", "INTEGER"),
            _col("cols", "INTEGER"),
            _col("data", "BLOB"),
        ),
    ),
    TableDef(
        "matches",
        (
            _col("pair_id", "INTEGER"),
            _col("rows", "INTEGER"),
            _col("cols", "INTEGER"),
            _col("data", "BLOB"),
        ),
    ),
    TableDef(
        "two_view_geometries",
        (
            _col("pair_id", "INTEGER"),
            _col("rows", "INTEGER"),
            _col("cols", "INTEGER"),
            _col("data", "BLOB"),
            _col("config", "INTEGER"),
            _col("F", "BLOB"),
            _col("E", "BLOB"),
            _col("H", "BLOB"),
            _col("qvec", "BLOB"),
            _col("tvec", "BLOB"),
        ),
    ),
    # ---- fork-specific extension tables (colmap_mod) ----
    TableDef(
        "videos",
        (
            _col("video_id", "INTEGER"),
            _col("name", "TEXT"),
            _col("source_path", "TEXT"),
            _col("content_hash", "TEXT"),
            _col("width", "INTEGER"),
            _col("height", "INTEGER"),
            _col("num_frames", "INTEGER"),
            _col("fps", "REAL"),
            _col("duration_seconds", "REAL"),
            _col("codec_name", "TEXT"),
            _col("sync_group", "TEXT"),
        ),
        extension=True,
        note="Video ingestion source metadata.",
    ),
    TableDef(
        "video_frames",
        (
            _col("video_id", "INTEGER"),
            _col("image_id", "INTEGER"),
            _col("frame_id", "INTEGER"),
            _col("pts_seconds", "REAL"),
            _col("time_id", "INTEGER"),
        ),
        extension=True,
        note="Maps decoded video frames to image ids.",
    ),
    TableDef(
        "image_qualities",
        (
            _col("image_id", "INTEGER"),
            _col("quality", "REAL"),
        ),
        extension=True,
        note="Per-image blur/sharpness (variance of Laplacian); written "
        "when ImageReaderOptions.estimate_quality is on.",
    ),
    TableDef(
        "markers",
        (
            _col("marker_id", "INTEGER"),
            _col("label", "TEXT"),
            _col("type", "INTEGER"),
            _col("world_position", "BLOB"),
            _col("world_position_cov", "BLOB"),
            _col("point3D_id", "INTEGER"),
            _col("enabled", "INTEGER"),
        ),
        extension=True,
        note="Named 3D points / ground-control points with optional world-coordinate priors.",
    ),
    TableDef(
        "marker_projections",
        (
            _col("marker_id", "INTEGER"),
            _col("image_id", "INTEGER"),
            _col("x", "REAL"),
            _col("y", "REAL"),
            _col("size", "REAL"),
            _col("pinned", "INTEGER"),
            _col("point2D_idx", "INTEGER"),
        ),
        extension=True,
        note="Per-image 2D projections of markers.",
    ),
)

COLMAP_DB_TABLES_BY_NAME: dict[str, TableDef] = {t.name: t for t in COLMAP_DB_TABLES}

#: Tables that exist only in the colmap_mod fork (not vanilla upstream).
EXTENSION_TABLES: frozenset[str] = frozenset(t.name for t in COLMAP_DB_TABLES if t.extension)

#: ``table.column`` extension columns added to otherwise-upstream tables.
EXTENSION_COLUMNS: frozenset[str] = frozenset(
    f"{t.name}.{c.name}" for t in COLMAP_DB_TABLES if not t.extension for c in t.extension_columns
)

#: Tables present in vanilla upstream COLMAP (the portable common core).
UPSTREAM_TABLES: frozenset[str] = frozenset(t.name for t in COLMAP_DB_TABLES if not t.extension)


def is_extension_table(name: str) -> bool:
    return name in EXTENSION_TABLES


def is_extension_column(table: str, column: str) -> bool:
    return f"{table}.{column}" in EXTENSION_COLUMNS


# --- serialized contract (the cross-tier parity artifact) -----------------
#
# contract_dict() renders the whole contract as a deterministic, language-
# neutral dict. It is the single thing the cross-tier parity machinery
# diffs: the Python source of truth is serialized here, the same bytes are
# embedded into the C++ port via codegen, and a check_sync gate fails if
# the two ever diverge. Ordering is the declaration order of
# COLMAP_DB_TABLES / columns, so the JSON is stable across runs.

CONTRACT_NAME = "colmap_db"
CONTRACT_SCHEMA_VERSION = 1  # version of THIS serialization shape, not the DB


def contract_dict() -> dict:
    """The COLMAP scene-database contract as a deterministic dict.

    This is the authoritative, repo-owned definition; ``tools/gen_contracts.py``
    serializes it to JSON + a C++ ``.inc``, and check_sync's ``contract-parity``
    gate enforces that the embedded C++ copy stays byte-identical.
    """
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "database_version": {
            "number": DATABASE_VERSION_NUMBER,
            "major": DATABASE_VERSION_MAJOR,
            "minor": DATABASE_VERSION_MINOR,
            "patch": DATABASE_VERSION_PATCH,
            "revision": DATABASE_SCHEMA_REVISION,
        },
        "pair_id": {"max_num_images": MAX_NUM_IMAGES},
        "extractor_types": {
            "known": dict(COLMAP_KNOWN_EXTRACTOR_TYPES),
            "undefined": UNDEFINED_EXTRACTOR_TYPE,
            "open_registry": True,
        },
        "matcher_types": {
            "known": list(COLMAP_KNOWN_MATCHER_TYPES),
            "open_registry": True,
        },
        "tables": [
            {
                "name": t.name,
                "extension": t.extension,
                "note": t.note,
                "columns": [
                    {
                        "name": c.name,
                        "sql_type": c.sql_type,
                        "extension": c.extension,
                        "note": c.note,
                    }
                    for c in t.columns
                ],
            }
            for t in COLMAP_DB_TABLES
        ],
        "upstream_tables": sorted(UPSTREAM_TABLES),
        "extension_tables": sorted(EXTENSION_TABLES),
        "extension_columns": sorted(EXTENSION_COLUMNS),
    }


__all__ = [
    "COLMAP_DB_TABLES",
    "COLMAP_DB_TABLES_BY_NAME",
    "COLMAP_KNOWN_EXTRACTOR_TYPES",
    "COLMAP_KNOWN_MATCHER_TYPES",
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "DATABASE_SCHEMA_REVISION",
    "DATABASE_VERSION_NUMBER",
    "EXTENSION_COLUMNS",
    "EXTENSION_TABLES",
    "MAX_NUM_IMAGES",
    "UNDEFINED_EXTRACTOR_TYPE",
    "UPSTREAM_TABLES",
    "ColumnDef",
    "TableDef",
    "contract_dict",
    "image_pair_to_pair_id",
    "is_colmap_native_extractor_type",
    "is_extension_column",
    "is_extension_table",
    "make_database_version_number",
    "matches_are_type_compatible",
    "pair_id_to_image_pair",
]
