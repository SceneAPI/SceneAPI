"""ProgressEvent v1 — discriminated union for the SSE stream.

Workers emit events to `events.jsonl` (one JSON line per event); the API
serves them via SSE with `Last-Event-ID` resume.

Schema is versioned: a `schema_version` literal is pinned per kind. New
kinds extend the union; old kinds never change shape.

.. rubric:: ProgressEvent kinds

:data:`ProgressEvent` is a tagged union discriminated on ``kind``:

- ``phase_started`` / ``phase_progress`` / ``phase_completed`` —
  per-phase lifecycle (``phase`` is one of :data:`Phase`).
- ``metric`` — scalar telemetry sample (``key`` + ``value``).
- ``snapshot_available`` — a sealed snapshot is now readable
  (``snapshot_seq`` + summary).
- ``log_line`` / ``warning`` / ``error`` — opaque message channels.

Forward-compatibility: SDKs MUST treat unknown ``kind`` values as
``unknown`` and surface them through a generic catch-all rather than
crashing the stream. New kinds are additive (new ``kind`` literal
plus new arm of the union); existing kinds never change shape.
``schema_version`` is pinned to ``1`` across every variant — bump it
in lock-step if the wire shape ever has to break, never per-kind.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

Phase = Literal[
    "feature_extraction",
    "matching",
    "geometric_verification",
    "incremental_init",
    "incremental_register",
    "incremental_ba",
    "global_rotation_avg",
    "global_positioning",
    "global_ba",
    "hierarchical_cluster",
    "hierarchical_merge",
    "panorama",
    "spherical",
    "bundle_adjust",
    "triangulate",
    "relocalize",
    "pose_graph_optimize",
    "segment",
    "export",
    "vlad_index",
    "backend_action",
    "artifact_conversion",
]


class _Base(BaseModel):
    schema_version: Literal[1] = 1
    ts: datetime
    job_id: str
    task_id: str | None = None
    seq: int


class PhaseStarted(_Base):
    kind: Literal["phase_started"] = "phase_started"
    phase: Phase


class PhaseProgress(_Base):
    kind: Literal["phase_progress"] = "phase_progress"
    phase: Phase
    current: int
    total: int | None = None
    rate: float | None = None  # items/sec


class PhaseCompleted(_Base):
    kind: Literal["phase_completed"] = "phase_completed"
    phase: Phase


class Metric(_Base):
    kind: Literal["metric"] = "metric"
    key: str
    value: float


class SnapshotAvailable(_Base):
    kind: Literal["snapshot_available"] = "snapshot_available"
    snapshot_seq: int
    summary: dict[str, Any]


class LogLine(_Base):
    kind: Literal["log_line"] = "log_line"
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    message: str


class Warning_(_Base):
    kind: Literal["warning"] = "warning"
    message: str


class ErrorEvent(_Base):
    kind: Literal["error"] = "error"
    error_class: str
    message: str
    detail: dict[str, Any] | None = None


ProgressEvent = Annotated[
    PhaseStarted
    | PhaseProgress
    | PhaseCompleted
    | Metric
    | SnapshotAvailable
    | LogLine
    | Warning_
    | ErrorEvent,
    Field(discriminator="kind"),
]


__all__ = [
    "ErrorEvent",
    "LogLine",
    "Metric",
    "Phase",
    "PhaseCompleted",
    "PhaseProgress",
    "PhaseStarted",
    "ProgressEvent",
    "SnapshotAvailable",
    "Warning_",
]
