"""Job request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.api.common import Link
from app.schemas.api.scene import Sim3

JobStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "cancelled_dirty",
]
"""Closed set of job lifecycle states (AIP-216).

Terminal states: ``succeeded`` | ``failed`` | ``cancelled`` |
``cancelled_dirty`` — see ``L13`` in ``docs/guides/decisions.md`` and
``app/workers/dispatcher.py::_maybe_finalize_job``."""

TaskStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "cancelled_dirty",
    "skipped",
]
"""Per-task lifecycle states. ``skipped`` covers cache hits."""


class JobOut(BaseModel):
    """Wire shape of a Job row.

    A Job is a long-running operation rolled up from N constituent
    Task rows (see :class:`TaskOut`). ``status`` reaches a terminal
    state (see :data:`JobStatus`) once every Task is terminal; the
    rollup is driven by ``app/workers/dispatcher.py::_maybe_finalize_job``.
    ``cancel_requested`` flips when ``POST /v1/jobs/{id}:cancel``
    arrives; ``cancel_force`` flips when ``?force=true`` was passed.
    ``error_class`` / ``error_message`` are populated only when the
    job ends in ``failed``.
    """

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    job_id: str
    tenant_id: str
    project_id: str
    recipe: str
    status: JobStatus
    cancel_requested: bool
    cancel_force: bool
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_class: str | None = None
    error_message: str | None = None
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")


class TaskOut(BaseModel):
    """Wire shape of a Task row inside a Job.

    Each Task = one ARQ job (see ``L5`` in ``decisions.md``). ``kind``
    is the worker handler name (``extract`` | ``match`` | ``map`` |
    ...). ``cache_key`` is the content-addressed lookup key; tasks
    that hit cache transition straight to ``skipped``. ``outputs_ref``
    carries the typed result payload — clients read this once
    ``status`` is terminal (the localize / oneshot result lives here,
    for instance).
    """

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    task_id: str
    job_id: str
    kind: str
    status: TaskStatus
    cache_key: str
    inputs_hash: str
    params_hash: str
    outputs_ref: dict[str, object] | None = Field(
        default=None,
        validation_alias="outputs_ref_json",
    )


class JobDetail(JobOut):
    """``GET /v1/jobs/{job_id}`` body — :class:`JobOut` plus the full
    constituent task list. Use :class:`JobOut` (without ``tasks``) for
    list endpoints; use :class:`JobDetail` for single-job reads."""

    tasks: list[TaskOut] = []


class TaskProgressOut(BaseModel):
    """Per-task progress snapshot for polling clients.

    ``progress`` is a best-effort fraction in ``[0, 1]``. It is ``1``
    for terminal tasks, event-derived for running tasks when the
    latest ``phase_progress`` event carries ``current`` / ``total``,
    and ``0`` otherwise.
    """

    task_id: str
    kind: str
    status: TaskStatus
    progress: float = Field(..., ge=0.0, le=1.0)
    phase: str | None = None
    current: int | None = None
    total: int | None = None
    latest_event_id: int | None = None
    latest_event_kind: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float | None = None


class JobProgressOut(BaseModel):
    """Compact polling snapshot for job progress.

    This endpoint complements ``/events`` for dashboards and CLIs that
    prefer polling over holding an SSE connection open.
    """

    job_id: str
    recipe: str
    status: JobStatus
    progress: float = Field(..., ge=0.0, le=1.0)
    total_tasks: int
    completed_tasks: int
    task_counts: dict[str, int]
    current_task_id: str | None = None
    current_task_kind: str | None = None
    current_phase: str | None = None
    latest_event_id: int | None = None
    latest_event: dict[str, object] | None = None
    tasks: list[TaskProgressOut] = []


class JobAcceptedResponse(BaseModel):
    """Canonical 202 envelope for endpoints that submit a Job.

    Returned by every ``POST`` that enqueues SfM work
    (`/datasets/{id}/features`, `/matches`, `/verify`, the
    `/pipelines/{recipe}` recipes, and the localize / dense / mesh /
    cubemap stages). Clients should follow ``Location`` to ``GET
    /v1/jobs/{job_id}`` for status.

    Stage-specific optional fields are typed here so SDK codegen can
    surface them as named accessors:

    - ``recon_id`` — endpoints nested under a reconstruction
    - ``dataset_id`` / ``project_id`` — parent-pointer for top-level routes
    - ``method`` — mesh submission method
    - ``applied_sim3`` — georegister applied transform
    - ``target_recon_id`` / ``source_recon_ids`` — ``reconstructions:merge``
    - ``strategy`` — ``similarity:build``
    - ``action_id`` / ``backend`` — backend-native extension actions
    """

    job_id: str
    task_ids: list[str] = Field(default_factory=list)
    recon_id: str | None = None
    dataset_id: str | None = None
    project_id: str | None = None
    method: str | None = None
    applied_sim3: Sim3 | None = None
    target_recon_id: str | None = None
    source_recon_ids: list[str] | None = None
    strategy: str | None = None
    action_id: str | None = None
    backend: str | None = None
    artifact_id: str | None = None
    target_format: str | None = None
