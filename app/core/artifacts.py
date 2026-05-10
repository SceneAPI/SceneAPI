"""Shared stage-artifact vocabulary and validation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

ARTIFACT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


@dataclass(frozen=True)
class ArtifactKindDefinition:
    kind: str
    title: str
    description: str
    durable: bool


CORE_ARTIFACT_KINDS: dict[str, ArtifactKindDefinition] = {
    "features.database": ArtifactKindDefinition(
        kind="features.database",
        title="Feature database",
        description="Feature descriptors and keypoints in a backend-readable database.",
        durable=False,
    ),
    "matches.database": ArtifactKindDefinition(
        kind="matches.database",
        title="Match database",
        description="Raw feature matches in a backend-readable database.",
        durable=False,
    ),
    "matches.correspondence_graph": ArtifactKindDefinition(
        kind="matches.correspondence_graph",
        title="Correspondence graph",
        description="Raw pairwise correspondence graph sidecar.",
        durable=False,
    ),
    "matches.two_view_geometries": ArtifactKindDefinition(
        kind="matches.two_view_geometries",
        title="Two-view geometries",
        description="Verified two-view geometry sidecar.",
        durable=False,
    ),
    "matches.verified_database": ArtifactKindDefinition(
        kind="matches.verified_database",
        title="Verified match database",
        description="Geometrically verified matches in a backend-readable database.",
        durable=False,
    ),
    "reconstruction.snapshot": ArtifactKindDefinition(
        kind="reconstruction.snapshot",
        title="Sealed reconstruction snapshot",
        description="Immutable sealed snapshot directory for a reconstruction.",
        durable=True,
    ),
    "reconstruction.submodel": ArtifactKindDefinition(
        kind="reconstruction.submodel",
        title="Reconstruction submodel",
        description="One disconnected mapping component inside a reconstruction snapshot.",
        durable=True,
    ),
}

DATABASE_ARTIFACT_KIND_BY_TASK: dict[str, str] = {
    "extract": "features.database",
    "match": "matches.database",
    "verify": "matches.verified_database",
}

ARTIFACT_INPUT_ROLE_KINDS: dict[str, frozenset[str]] = {
    "features": frozenset({"features.database"}),
    "pairs": frozenset({"matches.correspondence_graph"}),
    "matches": frozenset(
        {
            "matches.database",
            "matches.correspondence_graph",
            "matches.two_view_geometries",
        }
    ),
    "verified_matches": frozenset(
        {
            "matches.verified_database",
            "matches.two_view_geometries",
        }
    ),
    "snapshot": frozenset({"reconstruction.snapshot"}),
    "submodel": frozenset({"reconstruction.submodel"}),
}


def is_valid_artifact_key(value: str) -> bool:
    return bool(ARTIFACT_KEY_RE.fullmatch(value))


def is_core_artifact_kind(value: str) -> bool:
    return value in CORE_ARTIFACT_KINDS
