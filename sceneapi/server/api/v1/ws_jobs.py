"""WebSocket endpoint for live job event streaming + cancel.

Wire format (JSON-encoded text frames):

  Server -> client:
    { "kind": "hello", "job_id": "...", "last_event_id": 42 }
    { "kind": "<ProgressEvent.kind>", ...event payload }
    { "kind": "terminal", "status": "succeeded" | ... }

  Client -> server:
    { "op": "cancel", "force": false }
    { "op": "ping" }              # server replies with {"kind":"pong"}

Resume: clients pass `?last_event_id=N` to replay from the ring buffer
(mirrors the SSE endpoint).

This is light by design. Most consumers should still use SSE
(`GET /v1/jobs/{jid}/events`); WebSocket exists for browser viewers
that want bidirectional cancel + peek without an HTTP DELETE.
"""

from __future__ import annotations

import asyncio
import json as _json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy import select

from sceneapi.server.core.logging import get_logger
from sceneapi.server.db.models import Job, JobEvent
from sceneapi.server.db.session import get_session_factory

_log = get_logger("sceneapi.ws_jobs")

router = APIRouter(prefix="/ws/v1/jobs", tags=["jobs-ws"])


@router.get("/{job_id}", include_in_schema=False)
async def info(job_id: str) -> JSONResponse:
    """Plain GET hint: WebSocket lives at the same path."""
    return JSONResponse(
        {
            "kind": "ws_endpoint",
            "job_id": job_id,
            "ws_url": f"/ws/v1/jobs/{job_id}",
        }
    )


@router.websocket("/{job_id}")
async def ws_jobs(
    websocket: WebSocket,
    job_id: str,
    last_event_id: int = Query(default=0, ge=0),
) -> None:
    """WebSocket: stream events + accept cancel ops for a job.

    Bidirectional alternative to the SSE endpoint. On connect the
    server sends ``{"kind": "hello", ...}``; the client may send
    ``{"op": "cancel", "force": bool}`` or ``{"op": "ping"}``. Live
    events tail with ``?last_event_id=N`` resume semantics. Stream
    closes with code 1000 once the job hits a terminal state — same
    drain-once-then-EOF protocol as SSE (see ``L14``).
    """
    await websocket.accept()
    factory = get_session_factory()

    async with factory() as session:
        job = (await session.execute(select(Job).where(Job.job_id == job_id))).scalar_one_or_none()
    if job is None:
        await websocket.send_text(
            _json.dumps({"kind": "error", "message": f"job {job_id} not found"})
        )
        await websocket.close(code=1008)
        return

    cursor = int(last_event_id or 0)
    await websocket.send_text(
        _json.dumps({"kind": "hello", "job_id": job_id, "last_event_id": cursor})
    )

    stop_pumping = asyncio.Event()
    pump_task = asyncio.create_task(
        _pump_events(websocket, job_id=job_id, start_cursor=cursor, stop=stop_pumping)
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = _json.loads(raw)
            except _json.JSONDecodeError:
                await websocket.send_text(
                    _json.dumps({"kind": "error", "message": "expected JSON text frames"})
                )
                continue
            op = (msg or {}).get("op")
            if op == "ping":
                await websocket.send_text(_json.dumps({"kind": "pong"}))
            elif op == "cancel":
                force = bool((msg or {}).get("force"))
                async with factory() as session:
                    target = await session.get(Job, job_id)
                    if target is not None:
                        target.cancel_requested = True
                        if force:
                            target.cancel_force = True
                        await session.commit()
                await websocket.send_text(_json.dumps({"kind": "cancel_requested", "force": force}))
            else:
                await websocket.send_text(
                    _json.dumps({"kind": "error", "message": f"unknown op: {op!r}"})
                )
    except WebSocketDisconnect:
        pass
    finally:
        stop_pumping.set()
        try:
            await asyncio.wait_for(pump_task, timeout=2.0)
        except (TimeoutError, Exception):
            pump_task.cancel()


async def _pump_events(
    websocket: WebSocket,
    *,
    job_id: str,
    start_cursor: int,
    stop: asyncio.Event,
    poll_interval: float = 1.0,
) -> None:
    """Tail the job_event table; stop when the job hits a terminal state."""
    factory = get_session_factory()
    cursor = start_cursor
    terminal = {"succeeded", "failed", "cancelled", "cancelled_dirty"}
    while not stop.is_set():
        new_events: list[tuple[int, dict[str, Any]]] = []
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(JobEvent)
                        .where(JobEvent.job_id == job_id, JobEvent.event_id > cursor)
                        .order_by(JobEvent.event_id)
                        .limit(500)
                    )
                )
                .scalars()
                .all()
            )
            for ev in rows:
                cursor = ev.event_id
                new_events.append((ev.event_id, dict(ev.payload_json or {})))
            job = await session.get(Job, job_id)
            job_status = job.status if job else None
        for eid, payload in new_events:
            payload.setdefault("event_id", eid)
            try:
                await websocket.send_text(_json.dumps(payload))
            except Exception:
                stop.set()
                return
        if job_status in terminal:
            try:
                await websocket.send_text(_json.dumps({"kind": "terminal", "status": job_status}))
                await websocket.close(code=1000)
            except Exception as exc:
                # Client usually vanished between the last event and the
                # terminal frame — nothing to deliver to. Swallow (the
                # stream is over either way) but leave a trace.
                _log.debug("sceneapi.ws_terminal_send_failed", job_id=job_id, error=str(exc))
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval)
        except TimeoutError:
            continue
