"""Ergonomic helpers layered on top of the auto-generated client.

The generated SDK is intentionally minimal: it exposes typed
`from_dict` / `to_dict` decoders and per-endpoint API methods, plus a
single `UnexpectedStatus` exception. This module adds the
typed-exception hierarchy, the `supports()` capability helper, the
chunked-upload convenience, the SSE event iterator, and the binary
wire-format parsers that the hand-rolled `sfmapi_client` package
ships, so consumers of the generated SDK don't have to reinvent them.

This file is OWNED by the repo (preserved across `regen_sdk.py`
runs); it does NOT come from the generator.
"""

from __future__ import annotations

import contextlib
import json
import struct
from typing import Any, BinaryIO

import httpx

from .errors import UnexpectedStatus
from .models.capabilities_out import CapabilitiesOut

# ---------------------------------------------------------------------
# Typed exception hierarchy. Mirrors the hand-rolled SDK so callers
# can write `except SfmApiError:` regardless of which SDK they use.
# ---------------------------------------------------------------------


class SfmApiError(Exception):
    """Base for every typed sfmapi error."""

    def __init__(self, status_code: int, detail: str = "", body: dict[str, Any] | None = None):
        super().__init__(detail or f"sfmapi error: {status_code}")
        self.status_code = status_code
        self.detail = detail
        self.body = body or {}


class NotFoundError(SfmApiError):
    pass


class ConflictError(SfmApiError):
    pass


class ValidationError(SfmApiError):
    pass


class AuthError(SfmApiError):
    pass


class QuotaExceededError(SfmApiError):
    pass


class StorageError(SfmApiError):
    pass


class PycolmapUnavailableError(SfmApiError):
    pass


class TransportError(SfmApiError):
    pass


_BY_STATUS: dict[int, type[SfmApiError]] = {
    400: ValidationError,
    401: AuthError,
    403: AuthError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: QuotaExceededError,
    501: PycolmapUnavailableError,
}


def raise_for_status(exc: UnexpectedStatus) -> None:
    """Re-raise an :class:`UnexpectedStatus` as a typed
    :class:`SfmApiError` subclass based on the status code and the
    RFC7807 ``problem+json`` body when present."""
    body: dict[str, Any] = {}
    detail = ""
    try:
        body = json.loads(exc.content.decode("utf-8", errors="replace") or "{}")
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("title") or "")
    except (ValueError, UnicodeDecodeError):
        pass
    cls = _BY_STATUS.get(exc.status_code, SfmApiError)
    raise cls(exc.status_code, detail=detail, body=body) from exc


# ---------------------------------------------------------------------
# Capability helper.
# ---------------------------------------------------------------------


def supports(caps: CapabilitiesOut, capability: str) -> bool:
    """Return True iff the capabilities snapshot advertises ``capability``.

    Mirrors the hand-rolled SDK's ``Capabilities.supports()`` helper.
    Treats unknown / absent feature names as False (per the wire
    spec — clients MUST treat absence as unsupported).
    """
    feats = getattr(caps, "features", None)
    if feats is None:
        return False
    val = getattr(feats, "additional_properties", {}).get(capability)
    return bool(val) if val is not None else False


DEFAULT_CHUNK_SIZE = 1 * 1024 * 1024  # 1 MiB


# ---------------------------------------------------------------------
# SSE event iterator. Mirrors the hand-rolled
# `SfmApiClient.stream_events()` and the C++ `Client::ParseEventsBuffer`.
# ---------------------------------------------------------------------


from collections.abc import Iterator  # noqa: E402  — keep section grouped
from dataclasses import dataclass  # noqa: E402


@dataclass
class SseEvent:
    """Single Server-Sent Event from ``GET /v1/jobs/{job_id}/events``.

    ``data`` is the raw payload as a string. Most sfmapi events are
    JSON-encoded :class:`ProgressEvent` rows; consumers can call
    ``json.loads(event.data)`` to decode.
    """

    id: str = ""
    event: str = "message"
    data: str = ""

    def json(self) -> Any:
        """Decode ``data`` as JSON. Raises :class:`ValueError` on
        malformed payloads."""
        return json.loads(self.data)


def parse_sse_buffer(body: str) -> list[SseEvent]:
    """Decode an already-buffered SSE response body. Useful when a
    test or proxy has captured the full stream as a string."""
    out: list[SseEvent] = []
    cur = SseEvent()
    have_data = False
    for raw in body.splitlines():
        line = raw.rstrip("\r")
        if not line:
            if have_data:
                out.append(cur)
                cur = SseEvent()
                have_data = False
            continue
        if line.startswith(":"):
            continue  # SSE comment
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            cur.data = (cur.data + "\n" + value) if cur.data else value
            have_data = True
        elif field == "event":
            cur.event = value
        elif field == "id":
            cur.id = value
    if have_data:
        out.append(cur)
    return out


def stream_events(
    base_url: str,
    job_id: str,
    *,
    api_key: str | None = None,
    last_event_id: int | str | None = None,
    timeout: float = 600.0,
) -> Iterator[SseEvent]:
    """Stream Server-Sent Events from ``GET /v1/jobs/{job_id}/events``.

    Yields :class:`SseEvent` instances one at a time. Pass
    ``last_event_id`` to resume from a specific event (the server
    honors the standard ``Last-Event-ID`` header).

    Raises a typed :class:`SfmApiError` subclass on any non-2xx open.
    """
    headers: dict[str, str] = {"Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    base = base_url.rstrip("/")
    url = f"{base}/v1/jobs/{job_id}/events"
    with (
        httpx.Client(timeout=timeout, headers=headers) as client,
        client.stream("GET", url) as resp,
    ):
        if resp.status_code != 200:
            # Drain so the connection can be reused, then translate.
            resp.read()
            raise buildhttp_error(resp)
        buffer = ""
        for chunk in resp.iter_text():
            buffer += chunk
            while "\n\n" in buffer or "\r\n\r\n" in buffer:
                sep = "\r\n\r\n" if "\r\n\r\n" in buffer else "\n\n"
                head, _, buffer = buffer.partition(sep)
                yield from parse_sse_buffer(head + sep)


def buildhttp_error(resp: httpx.Response) -> SfmApiError:
    """Build a typed :class:`SfmApiError` subclass from any
    :class:`httpx.Response`. Used by the upload helpers below; also
    available to consumers writing their own request flows on top of
    the generated SDK without going through ``UnexpectedStatus``."""
    body: dict[str, Any] = {}
    try:
        decoded = resp.json()
        if isinstance(decoded, dict):
            body = decoded
    except (ValueError, json.JSONDecodeError):
        pass
    detail = ""
    if body:
        detail = str(body.get("detail") or body.get("title") or "")
    cls = _BY_STATUS.get(resp.status_code, SfmApiError)
    return cls(resp.status_code, detail=detail, body=body)


def upload_bytes(
    base_url: str,
    data: bytes,
    *,
    api_key: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    content_type: str = "application/octet-stream",
    timeout: float = 60.0,
) -> str:
    """Drive the chunked-upload protocol end-to-end and return the
    resulting ``blob_sha``.

    Mirrors the hand-rolled ``SfmApiClient.upload_bytes()`` and the
    C++ ``Client::UploadBytes()``. Steps:
      1. ``POST /v1/uploads`` -> ``upload_id``.
      2. ``PATCH /v1/uploads/{id}`` for each chunk with ``Content-Range``.
      3. ``POST /v1/uploads/{id}:finalize`` -> ``blob_sha``.

    Raises a typed :class:`SfmApiError` subclass on any non-2xx.
    """
    if not data:
        raise ValueError("upload_bytes: data must be non-empty")
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    base = base_url.rstrip("/")
    with httpx.Client(timeout=timeout, headers=headers) as client:
        init = client.post(
            f"{base}/v1/uploads",
            json={"expected_size": len(data), "content_type": content_type},
        )
        if init.status_code not in (200, 201):
            raise buildhttp_error(init)
        upload_id = init.json()["upload_id"]
        offset = 0
        total = len(data)
        while offset < total:
            chunk = data[offset : offset + chunk_size]
            end = offset + len(chunk) - 1
            r = client.patch(
                f"{base}/v1/uploads/{upload_id}",
                content=chunk,
                headers={
                    "Content-Range": f"bytes {offset}-{end}/{total}",
                    "Content-Type": "application/octet-stream",
                },
            )
            if r.status_code not in (200, 204):
                raise buildhttp_error(r)
            offset += len(chunk)
        fin = client.post(f"{base}/v1/uploads/{upload_id}:finalize")
        if fin.status_code not in (200, 201):
            raise buildhttp_error(fin)
        sha: str = fin.json()["blob_sha"]
        return sha


def upload_file(
    base_url: str,
    fileobj: BinaryIO,
    *,
    api_key: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    content_type: str = "application/octet-stream",
    timeout: float = 60.0,
) -> str:
    """Read ``fileobj`` fully into memory and call :func:`upload_bytes`.

    For very large files prefer streaming via the hand-rolled SDK's
    async client; this helper keeps the generated SDK self-contained
    without pulling in extra streaming machinery.
    """
    return upload_bytes(
        base_url,
        fileobj.read(),
        api_key=api_key,
        chunk_size=chunk_size,
        content_type=content_type,
        timeout=timeout,
    )


# ---------------------------------------------------------------------
# wait_for_job — block until a Job reaches a terminal state.
#
# Combines the SSE event stream with periodic GET /v1/jobs/{id}
# polling so the helper works against servers that have or don't
# have terminal-state SSE events. The polling interval is short
# enough that consumers don't perceive latency, but long enough
# that the server isn't hammered. Yields each ProgressEvent as a
# side channel via ``on_event`` if provided.
# ---------------------------------------------------------------------


TERMINAL_JOB_STATES: frozenset[str] = frozenset(
    {"succeeded", "failed", "cancelled", "cancelled_dirty"}
)


def wait_for_job(
    base_url: str,
    job_id: str,
    *,
    api_key: str | None = None,
    on_event: Any = None,  # callable(SseEvent) -> None
    poll_interval: float = 0.25,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Block until ``job_id`` reaches a terminal state and return
    the final ``JobDetail`` JSON body.

    The implementation uses lightweight polling rather than SSE so
    the helper has no streaming dependency. Consumers that want
    progress events should call :func:`stream_events` in parallel
    or pass an ``on_event`` callback (the helper polls
    ``/v1/jobs/{id}/events?last_event_id=X`` once per loop and
    invokes the callback for each new event).

    Raises :class:`TimeoutError` if the job is still non-terminal
    after ``timeout`` seconds.
    Raises a typed :class:`SfmApiError` subclass on any non-2xx.
    """
    import time

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    base = base_url.rstrip("/")
    deadline = time.monotonic() + timeout
    seen_event_id = -1
    with httpx.Client(timeout=30.0, headers=headers) as client:
        while True:
            r = client.get(f"{base}/v1/jobs/{job_id}")
            if r.status_code != 200:
                raise buildhttp_error(r)
            body = r.json()
            status = str(body.get("status") or "")
            if on_event is not None:
                # Best-effort SSE poll for new events; tolerate failures
                # so the wait loop survives transient SSE hiccups.
                try:
                    headers_loop = {"Accept": "text/event-stream"}
                    if seen_event_id >= 0:
                        headers_loop["Last-Event-ID"] = str(seen_event_id)
                    er = client.get(
                        f"{base}/v1/jobs/{job_id}/events",
                        headers=headers_loop,
                        timeout=2.0,
                    )
                    if er.status_code == 200:
                        for ev in parse_sse_buffer(er.text):
                            on_event(ev)
                            if ev.id:
                                with contextlib.suppress(ValueError):
                                    seen_event_id = max(seen_event_id, int(ev.id))
                except httpx.HTTPError:
                    pass
            if status in TERMINAL_JOB_STATES:
                return body
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"wait_for_job: {job_id} still in status={status!r} after {timeout}s"
                )
            time.sleep(poll_interval)


# ---------------------------------------------------------------------
# submit_and_stream — like submit_and_wait but consumes the SSE event
# stream live (sub-second progress) instead of polling. The generator
# yields each ProgressEvent as it arrives; the final JobDetail is
# attached to the StopIteration value (PEP 380) so consumers can
# `result = yield from gen` to grab it.
# ---------------------------------------------------------------------


def submit_and_stream(
    base_url: str,
    submit_fn: Any,  # callable() -> dict-like with a `job_id` key
    *,
    api_key: str | None = None,
    timeout: float = 600.0,
) -> Iterator[SseEvent]:
    """Run ``submit_fn`` to enqueue a job, then yield each SSE event
    from ``GET /v1/jobs/{job_id}/events`` in real time. After the
    stream closes, fetches the terminal :class:`JobDetail` once via
    ``wait_for_job`` (with a tight poll interval) and attaches it to
    the generator's return value.

    Usage::

        gen = submit_and_stream(base, submit_fn)
        for event in gen:
            handle_progress(event)
        final = gen.value  # type: dict[str, Any]
    """
    accepted = submit_fn()
    job_id: str | None = None
    if isinstance(accepted, dict):
        job_id = accepted.get("job_id")
    else:
        job_id = getattr(accepted, "job_id", None)
    if not job_id:
        raise ValueError(
            f"submit_and_stream: submit_fn returned no job_id (got {type(accepted).__name__!s})"
        )
    yield from stream_events(base_url, job_id, api_key=api_key, timeout=timeout)
    # The SSE stream closes when the server side finishes flushing.
    # Pick up the terminal JobDetail via a single wait_for_job poll.
    return wait_for_job(base_url, job_id, api_key=api_key, poll_interval=0.05, timeout=timeout)


# ---------------------------------------------------------------------
# submit_and_wait — fire any stage-submit callable, then block on
# `wait_for_job` for the resulting `job_id`. The most-used end-to-end
# consumer flow: "do the thing, give me the final JobDetail".
# ---------------------------------------------------------------------


def submit_and_wait(
    base_url: str,
    submit_fn: Any,  # callable() -> dict-like with a `job_id` key
    *,
    api_key: str | None = None,
    on_event: Any = None,  # callable(SseEvent) -> None
    poll_interval: float = 0.25,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Run ``submit_fn`` to enqueue a job, then block until the
    returned ``job_id`` reaches a terminal state. Returns the final
    ``JobDetail`` body.

    ``submit_fn`` is any zero-arg callable that returns either a
    ``JobAcceptedResponse``-shaped dict or any object exposing a
    ``job_id`` attribute / key. This covers both the typed generated
    API methods (``submit_features.sync(client=...)``) and any hand-
    written request that returns the canonical 202 envelope.

    Raises a typed :class:`SfmApiError` subclass on any non-2xx
    poll, or :class:`TimeoutError` on timeout.
    """
    accepted = submit_fn()
    job_id: str | None = None
    if isinstance(accepted, dict):
        job_id = accepted.get("job_id")
    else:
        job_id = getattr(accepted, "job_id", None)
    if not job_id:
        raise ValueError(
            f"submit_and_wait: submit_fn returned no job_id (got {type(accepted).__name__!s})"
        )
    return wait_for_job(
        base_url,
        job_id,
        api_key=api_key,
        on_event=on_event,
        poll_interval=poll_interval,
        timeout=timeout,
    )


# ---------------------------------------------------------------------
# Binary wire-format parsers. Mirror `clients/python/sfmapi_client/binary.py`
# (and `app/schemas/points_binary.py` / `app/schemas/depth_map_binary.py`
# on the server). Pure-stdlib (struct + bytes); no NumPy required.
# ---------------------------------------------------------------------


POINTS_MAGIC = b"SFMP3D\x00\x00"
DEPTH_MAGIC = b"SFMDPTH\x00"
NORMAL_MAGIC = b"SFMNRM\x00\x00"

POINTS_HEADER_SIZE = 44
POINTS_RECORD_SIZE = 26
MAP_HEADER_SIZE = 32

POINTS_MEDIA_TYPE = "application/x-sfm-points-v1"
DEPTH_MEDIA_TYPE = "application/x-sfm-depth-v1"
NORMAL_MEDIA_TYPE = "application/x-sfm-normal-v1"


class WireFormatError(ValueError):
    """Raised when a binary blob fails magic / version / length checks."""


@dataclass(frozen=True)
class Point3DRecord:
    point3d_id: int
    xyz: tuple[float, float, float]
    rgb: tuple[int, int, int]
    track_len: int


@dataclass
class PointsBinary:
    count: int
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    records: list[Point3DRecord]


def parse_points_binary(data: bytes) -> PointsBinary:
    """Decode an ``application/x-sfm-points-v1`` blob."""
    if len(data) < POINTS_HEADER_SIZE:
        raise WireFormatError("points-binary: buffer too small for header")
    if data[:8] != POINTS_MAGIC:
        raise WireFormatError("points-binary: bad magic")
    version = struct.unpack_from("<I", data, 8)[0]
    if version != 1:
        raise WireFormatError(f"points-binary: unknown version {version}")
    count = struct.unpack_from("<Q", data, 12)[0]
    bbox_min = struct.unpack_from("<fff", data, 20)
    bbox_max = struct.unpack_from("<fff", data, 32)
    expected = POINTS_HEADER_SIZE + count * POINTS_RECORD_SIZE
    if len(data) < expected:
        raise WireFormatError(f"points-binary: body short - got {len(data)}, expected {expected}")
    records: list[Point3DRecord] = []
    for i in range(count):
        off = POINTS_HEADER_SIZE + i * POINTS_RECORD_SIZE
        x, y, z, r, g, b, _pad, tl, pid = struct.unpack_from("<fffBBBBHQ", data, off)
        records.append(
            Point3DRecord(
                point3d_id=pid,
                xyz=(x, y, z),
                rgb=(r, g, b),
                track_len=tl,
            )
        )
    return PointsBinary(count=count, bbox_min=bbox_min, bbox_max=bbox_max, records=records)


@dataclass
class DepthMap:
    width: int
    height: int
    depth_min: float
    depth_max: float
    pixels: bytes  # raw float32 little-endian, length = width*height*4


def parse_depth_map(data: bytes) -> DepthMap:
    """Decode an ``application/x-sfm-depth-v1`` blob.

    ``pixels`` is the raw float32 little-endian byte buffer of length
    ``width * height * 4``. Use ``struct``, ``array.array("f")``, or
    ``np.frombuffer(pixels, dtype="<f4")`` to materialize it.
    """
    if len(data) < MAP_HEADER_SIZE:
        raise WireFormatError("depth-binary: buffer too small for header")
    if data[:8] != DEPTH_MAGIC:
        raise WireFormatError("depth-binary: bad magic")
    version = struct.unpack_from("<I", data, 8)[0]
    if version != 1:
        raise WireFormatError(f"depth-binary: unknown version {version}")
    w, h = struct.unpack_from("<II", data, 12)
    dmin, dmax = struct.unpack_from("<ff", data, 20)
    expected = MAP_HEADER_SIZE + w * h * 4
    if len(data) < expected:
        raise WireFormatError(f"depth-binary: body short - got {len(data)}, expected {expected}")
    pixels = bytes(data[MAP_HEADER_SIZE : MAP_HEADER_SIZE + w * h * 4])
    return DepthMap(width=w, height=h, depth_min=dmin, depth_max=dmax, pixels=pixels)


@dataclass
class NormalMap:
    width: int
    height: int
    pixels: bytes  # raw float32 little-endian, length = width*height*3*4


def parse_normal_map(data: bytes) -> NormalMap:
    """Decode an ``application/x-sfm-normal-v1`` blob."""
    if len(data) < MAP_HEADER_SIZE:
        raise WireFormatError("normal-binary: buffer too small for header")
    if data[:8] != NORMAL_MAGIC:
        raise WireFormatError("normal-binary: bad magic")
    version = struct.unpack_from("<I", data, 8)[0]
    if version != 1:
        raise WireFormatError(f"normal-binary: unknown version {version}")
    w, h = struct.unpack_from("<II", data, 12)
    expected = MAP_HEADER_SIZE + w * h * 3 * 4
    if len(data) < expected:
        raise WireFormatError(f"normal-binary: body short - got {len(data)}, expected {expected}")
    pixels = bytes(data[MAP_HEADER_SIZE : MAP_HEADER_SIZE + w * h * 3 * 4])
    return NormalMap(width=w, height=h, pixels=pixels)


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "DEPTH_MAGIC",
    "DEPTH_MEDIA_TYPE",
    "MAP_HEADER_SIZE",
    "NORMAL_MAGIC",
    "NORMAL_MEDIA_TYPE",
    "POINTS_HEADER_SIZE",
    "POINTS_MAGIC",
    "POINTS_MEDIA_TYPE",
    "POINTS_RECORD_SIZE",
    "TERMINAL_JOB_STATES",
    "AuthError",
    "ConflictError",
    "DepthMap",
    "NormalMap",
    "NotFoundError",
    "Point3DRecord",
    "PointsBinary",
    "PycolmapUnavailableError",
    "QuotaExceededError",
    "SfmApiError",
    "SseEvent",
    "StorageError",
    "TransportError",
    "ValidationError",
    "WireFormatError",
    "buildhttp_error",
    "parse_depth_map",
    "parse_normal_map",
    "parse_points_binary",
    "parse_sse_buffer",
    "raise_for_status",
    "stream_events",
    "submit_and_stream",
    "submit_and_wait",
    "supports",
    "upload_bytes",
    "upload_file",
    "wait_for_job",
]
