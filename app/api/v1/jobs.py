"""Job routes — status, events (SSE), cancel."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import current_tenant
from app.db.models import JobEvent, Task
from app.db.session import get_db
from app.schemas.api.common import Page, to_out
from app.schemas.api.jobs import JobDetail, JobOut, JobProgressOut, JobStatus, TaskOut
from app.services import job_progress_service, job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=Page[JobOut])
async def list_(
    page_token: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=500),
    status: JobStatus | None = Query(
        None,
        description=(
            "Filter to one lifecycle state. "
            "Closed set: pending | running | succeeded | failed | "
            "cancelled | cancelled_dirty."
        ),
    ),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Page[JobOut]:
    """List jobs for the caller's tenant (AIP-158 paginated).

    Most-recent first (sorted by ``job_id`` descending — ULIDs are
    timestamp-prefixed). Pass ``status=running`` to find active work
    or ``status=failed`` to triage. Without ``status``, all jobs in
    every state are returned. ``next_page_token=null`` ends the
    cursor.
    """
    rows, next_page_token = await job_service.list_jobs(
        session,
        tenant_id=tenant_id,
        page_size=page_size,
        page_token=page_token,
        status=status,
    )
    return Page[JobOut](items=[to_out(JobOut, r) for r in rows], next_page_token=next_page_token)


@router.get("/{job_id}", response_model=JobDetail)
async def get(
    job_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JobDetail:
    """Read a job + its constituent tasks.

    The canonical AIP-151 LRO poll endpoint. Clients submitting work
    via any ``POST`` that returns ``202`` follow the ``Location``
    header here and poll until ``status`` reaches a terminal value
    (``succeeded`` | ``failed`` | ``cancelled`` | ``cancelled_dirty``).
    Task ``outputs_ref`` carries the typed result payload for stages
    that return data (e.g. ``localize``).
    """
    j = await job_service.get_job(session, tenant_id=tenant_id, job_id=job_id)
    tasks = (
        (await session.execute(select(Task).where(Task.job_id == job_id).order_by(Task.created_at)))
        .scalars()
        .all()
    )
    return JobDetail.model_validate(j).model_copy(
        update={"tasks": [to_out(TaskOut, t) for t in tasks]}
    )


@router.get("/{job_id}/progress", response_model=JobProgressOut)
async def progress(
    job_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JobProgressOut:
    """Return a compact progress snapshot for a job.

    This is the polling counterpart to ``GET /v1/jobs/{job_id}/events``.
    It always works from durable state: task lifecycle rows plus the
    latest persisted progress events. ``progress`` is a best-effort
    fraction, so clients should treat it as UI telemetry rather than a
    scheduling guarantee.
    """
    return await job_progress_service.get_job_progress(
        session,
        tenant_id=tenant_id,
        job_id=job_id,
    )


@router.post("/{job_id}:cancel", response_model=JobOut)
async def cancel(
    job_id: str,
    force: bool = Query(False),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JobOut:
    """Cooperatively cancel a long-running job (AIP-151, AIP-136
    ``:cancel``). ``force=true`` SIGKILLs subprocesses immediately;
    default is the cooperative phase-boundary stop.

    Returns the up-to-date ``JobOut`` row. The terminal state lands
    asynchronously — clients should follow up with ``GET
    /v1/jobs/{job_id}`` (or watch the SSE stream) to observe the
    transition to ``cancelled`` or ``cancelled_dirty``.
    """
    j = await job_service.cancel_job(session, tenant_id=tenant_id, job_id=job_id, force=force)
    return to_out(JobOut, j)


@router.get("/{job_id}/events", status_code=status.HTTP_200_OK)
async def events(
    job_id: str,
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE stream of progress events for the given job.

    Body
    ----
    ``Content-Type: text/event-stream``. Each event is one
    :class:`~app.schemas.progress_event.ProgressEvent` JSON-encoded
    in the SSE ``data:`` field, prefixed by an ``id:`` line carrying
    the monotonic per-job event sequence.

    Resume (``Last-Event-ID``)
    --------------------------
    Clients reconnecting after a transient disconnect SHOULD pass the
    last event id they observed via the standard ``Last-Event-ID``
    request header (browsers do this automatically). The server replays
    every persisted event with ``event_id > last_event_id`` from the
    ring buffer before resuming the live tail. Sending a value larger
    than any persisted id yields an empty replay and the live tail.

    Termination
    -----------
    The stream closes (server-side EOF) once the job's status reaches
    a terminal value (``succeeded`` | ``failed`` | ``cancelled`` |
    ``cancelled_dirty``) AND one final drain cycle has shipped any
    pending events. Without this exit condition, ``submit_and_stream``
    consumers would block forever waiting for EOF on a job that
    already finished. The terminal vocabulary is shared with
    ``app/workers/dispatcher.py::_maybe_finalize_job`` (see ``L13``,
    ``L14`` in ``decisions.md``).

    Mid-stream deletion
    -------------------
    If the underlying job row vanishes while the stream is open
    (e.g., tenant teardown, DB GC), the next poll cycle observes a
    ``None`` job and exits as if a terminal state were reached — the
    stream closes cleanly rather than 500-ing mid-flight. Clients see
    EOF; a follow-up ``GET /v1/jobs/{job_id}`` then returns 404.

    Phase 1 implementation tails by polling the DB on a 1s cadence;
    Phase 5 swaps to Redis pub/sub without changing the wire shape.
    """
    await job_service.get_job(session, tenant_id=tenant_id, job_id=job_id)

    async def gen() -> AsyncIterator[bytes]:
        from app.db.models import Job

        terminal_statuses = {"succeeded", "failed", "cancelled", "cancelled_dirty"}
        cursor = last_event_id or 0
        terminal_seen = False
        while True:
            rows = (
                (
                    await session.execute(
                        select(JobEvent)
                        .where(JobEvent.job_id == job_id, JobEvent.event_id > cursor)
                        .order_by(JobEvent.event_id)
                        .limit(1000)
                    )
                )
                .scalars()
                .all()
            )
            for ev in rows:
                cursor = ev.event_id
                payload = json.dumps(ev.payload_json, sort_keys=True)
                yield f"id: {ev.event_id}\n".encode()
                yield f"data: {payload}\n\n".encode()
            j = await session.get(Job, job_id)
            if j is not None and j.status in terminal_statuses:
                if terminal_seen:
                    return
                terminal_seen = True
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")
