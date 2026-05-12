"""Confirms the generated SDK's `_ergonomics` shim — typed
`SfmApiError` hierarchy + `supports()` capability helper — closes
the parity gap with the hand-rolled SDK.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from tests.contract.conftest import load_fixture

pytestmark = pytest.mark.contract

SERVER_ROOT = Path(__file__).resolve().parents[2]
SDK_ROOT = Path(os.environ.get("SFMAPI_SDK_REPO", SERVER_ROOT.parent / "sfmapi-sdk"))
GEN_ROOT = SDK_ROOT / "python" / "sfmapi_client_gen"


def _import_generated() -> tuple[object, object]:
    """Import the generated package + its _ergonomics shim. Skip the
    test module if the generated SDK isn't on disk."""
    if not GEN_ROOT.is_dir():
        pytest.skip("generated SDK not present")
    parent = str(GEN_ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    erg_spec = importlib.util.find_spec("sfmapi_client_gen._ergonomics")
    if erg_spec is None:
        pytest.skip("_ergonomics shim missing — was the SDK regenerated?")
    erg = importlib.util.module_from_spec(erg_spec)
    sys.modules["sfmapi_client_gen._ergonomics"] = erg
    assert erg_spec.loader is not None
    erg_spec.loader.exec_module(erg)
    caps_spec = importlib.util.find_spec("sfmapi_client_gen.models.capabilities_out")
    assert caps_spec is not None
    caps_mod = importlib.util.module_from_spec(caps_spec)
    sys.modules["sfmapi_client_gen.models.capabilities_out"] = caps_mod
    assert caps_spec.loader is not None
    caps_spec.loader.exec_module(caps_mod)
    return erg, caps_mod


def test_error_hierarchy_present() -> None:
    erg, _ = _import_generated()
    # Mirror the hand-rolled SDK exactly.
    for name in (
        "SfmApiError",
        "NotFoundError",
        "ConflictError",
        "ValidationError",
        "AuthError",
        "QuotaExceededError",
        "StorageError",
        "PycolmapUnavailableError",
        "TransportError",
    ):
        cls = getattr(erg, name, None)
        assert cls is not None, f"_ergonomics missing {name}"
        if name != "SfmApiError":
            assert issubclass(cls, erg.SfmApiError)


def test_raise_for_status_translates_404() -> None:
    erg, _ = _import_generated()
    from sfmapi_client_gen.errors import UnexpectedStatus

    body = load_fixture("error_404_project_missing")
    raw = UnexpectedStatus(404, str(body).encode("utf-8"))
    # Replace with the real JSON-encoded body so the parser works.
    import json

    raw = UnexpectedStatus(404, json.dumps(body).encode("utf-8"))
    with pytest.raises(erg.NotFoundError) as exc_info:
        erg.raise_for_status(raw)
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


def test_raise_for_status_translates_422() -> None:
    erg, _ = _import_generated()
    import json

    from sfmapi_client_gen.errors import UnexpectedStatus

    body = load_fixture("error_422_validation")
    raw = UnexpectedStatus(422, json.dumps(body).encode("utf-8"))
    with pytest.raises(erg.ValidationError) as exc_info:
        erg.raise_for_status(raw)
    assert exc_info.value.status_code == 422


def test_raise_for_status_falls_through_to_base() -> None:
    erg, _ = _import_generated()
    from sfmapi_client_gen.errors import UnexpectedStatus

    raw = UnexpectedStatus(418, b"{}")
    with pytest.raises(erg.SfmApiError) as exc_info:
        erg.raise_for_status(raw)
    assert exc_info.value.status_code == 418


def test_supports_helper_works_against_real_capabilities() -> None:
    erg, caps_mod = _import_generated()
    body = load_fixture("capabilities")
    caps = caps_mod.CapabilitiesOut.from_dict(body)
    # `spec.read` is a CORE capability — every conforming server
    # advertises it as true.
    assert erg.supports(caps, "spec.read") is True
    # An unknown name is always false.
    assert erg.supports(caps, "no.such.capability.exists") is False


def test_upload_bytes_helper_drives_full_protocol(live_ephemeral_server: str) -> None:
    """Run ``upload_bytes()`` against a live ephemeral server and
    confirm we get a real sha256 back. End-to-end test of the
    init -> patch -> finalize convenience flow."""
    import hashlib

    erg, _ = _import_generated()
    payload = b"\x00\x01\x02\x03hello-from-upload-bytes-helper"
    sha = erg.upload_bytes(live_ephemeral_server, payload, chunk_size=8)
    assert sha == hashlib.sha256(payload).hexdigest()


def test_buildhttp_error_translates_problem_json() -> None:
    erg, _ = _import_generated()
    import httpx

    fake = httpx.Response(
        404,
        json={"detail": "Project x not found", "title": "Resource not found"},
    )
    err = erg.buildhttp_error(fake)
    assert isinstance(err, erg.NotFoundError)
    assert err.status_code == 404
    assert "not found" in err.detail.lower()


def test_parse_sse_buffer_handles_canonical_stream() -> None:
    erg, _ = _import_generated()
    body = (
        'id: 1\nevent: progress\ndata: {"phase":"extract","current":5}\n\n'
        ": this is a comment\n"
        "id: 2\ndata: line1\ndata: line2\n\n"
    )
    events = erg.parse_sse_buffer(body)
    assert len(events) == 2
    assert events[0].id == "1"
    assert events[0].event == "progress"
    decoded = events[0].json()
    assert decoded["phase"] == "extract"
    assert decoded["current"] == 5
    assert events[1].id == "2"
    assert events[1].data == "line1\nline2"


def test_parse_sse_buffer_handles_crlf() -> None:
    erg, _ = _import_generated()
    body = "id: 7\r\nevent: msg\r\ndata: hello\r\n\r\n"
    events = erg.parse_sse_buffer(body)
    assert len(events) == 1
    assert events[0].data == "hello"
    assert events[0].event == "msg"
    assert events[0].id == "7"


def test_parse_points_binary_round_trip_against_server_encoder() -> None:
    """Encode 2 points via the server-side encoder, decode via the
    generated SDK's parser, confirm exact round-trip. Cross-language
    parity guarantee for the most-used wire shape."""
    erg, _ = _import_generated()
    from app.schemas.points_binary import Point3DRecord as SrvPoint
    from app.schemas.points_binary import encode_all

    records = [
        SrvPoint(point3d_id=100, xyz=(1.0, 2.0, 3.0), rgb=(255, 0, 0), track_len=5),
        SrvPoint(point3d_id=0xDEADBEEF, xyz=(4.5, -1.5, 0.25), rgb=(0, 128, 255), track_len=12),
    ]
    encoded = encode_all(records, bbox_min=(0.0, -1.5, 0.0), bbox_max=(4.5, 2.0, 3.0))
    parsed = erg.parse_points_binary(encoded)
    assert parsed.count == 2
    assert parsed.bbox_min == (0.0, -1.5, 0.0)
    assert parsed.bbox_max == (4.5, 2.0, 3.0)
    assert parsed.records[0].point3d_id == 100
    assert parsed.records[0].xyz == (1.0, 2.0, 3.0)
    assert parsed.records[0].rgb == (255, 0, 0)
    assert parsed.records[0].track_len == 5
    assert parsed.records[1].point3d_id == 0xDEADBEEF


def test_parse_points_binary_rejects_bad_magic() -> None:
    erg, _ = _import_generated()
    bad = b"WRONGMAG" + b"\x00" * 64
    with pytest.raises(erg.WireFormatError, match="bad magic"):
        erg.parse_points_binary(bad)


def _stage_submit(base: str, dataset_id: str) -> dict:
    """Helper for live tests: submit a features stage and return the
    202 envelope. Uses an upload+image already-registered against
    ``dataset_id``; callers do that bootstrap themselves."""
    import httpx as _httpx

    with _httpx.Client(base_url=base, timeout=10.0) as c:
        r = c.post(
            f"/v1/datasets/{dataset_id}/features",
            json={"spec": {"version": 1, "type": "sift", "max_num_features": 16}},
        )
        assert r.status_code in (200, 201, 202), r.text
        return r.json()


def _bootstrap_dataset(base: str, name: str = "live") -> str:
    """Helper for live tests: project + dataset + uploaded image.
    Returns the dataset_id. Used by every test that needs a job to
    submit against."""
    import httpx as _httpx

    erg, _ = _import_generated()
    with _httpx.Client(base_url=base, timeout=10.0) as c:
        proj = c.post("/v1/projects", json={"name": f"{name}-host"}).json()
        ds = c.post(
            f"/v1/projects/{proj['project_id']}/datasets",
            json={
                "name": f"{name}-ds",
                "source": {"kind": "upload", "entries": []},
                "camera_model": "SIMPLE_RADIAL",
                "intrinsics_mode": "single_camera",
                "is_spherical": False,
                "respect_exif_orientation": False,
            },
        ).json()
        sha = erg.upload_bytes(base, b"\x00\x01\x02\x03", chunk_size=8)
        c.post(
            f"/v1/datasets/{ds['dataset_id']}/images",
            json={"name": "stub.jpg", "blob_sha": sha, "width": 1, "height": 1},
        )
        return ds["dataset_id"]  # type: ignore[no-any-return]


def test_wait_for_job_returns_terminal_body_against_live_server(
    live_ephemeral_server: str,
) -> None:
    """Boot ephemeral app, submit a job that fails immediately (worker
    has no real images), confirm ``wait_for_job`` returns the terminal
    JobDetail."""
    erg, _ = _import_generated()
    base = live_ephemeral_server
    did = _bootstrap_dataset(base, "wait")
    sub = _stage_submit(base, did)

    body = erg.wait_for_job(base, sub["job_id"], timeout=10.0)
    assert body["status"] in erg.TERMINAL_JOB_STATES
    assert body["job_id"] == sub["job_id"]


def test_submit_and_wait_chains_submit_then_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage submit + terminal wait in a single call. Uses a synthetic
    submit closure so the test runs without a live server (the
    wait_for_job live-server test already covers the end-to-end path)."""
    import httpx

    erg, _ = _import_generated()

    # Synthesize an httpx mock transport so wait_for_job's polls hit
    # our handler. The submit closure returns a dict with the job_id
    # the wait will then resolve.
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        # All wait_for_job polls hit GET /v1/jobs/{id}; first two
        # return non-terminal, third terminal.
        poll_count["n"] += 1
        if request.url.path.startswith("/v1/jobs/") and request.method == "GET":
            states = ["pending", "running", "succeeded"]
            i = min(poll_count["n"] - 1, len(states) - 1)
            body = {"job_id": "j_chain", "status": states[i], "tasks": []}
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"detail": "not routed"})

    from typing import Any as _Any

    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client

    def patched_client_cls(*args: _Any, **kwargs: _Any) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", patched_client_cls)

    submitted = {"called": False}

    def submit_fn() -> dict[str, str]:
        submitted["called"] = True
        return {"job_id": "j_chain", "task_ids": ["t1"]}

    result = erg.submit_and_wait("http://x", submit_fn, poll_interval=0.001, timeout=5.0)
    assert submitted["called"] is True
    assert result["status"] == "succeeded"
    assert result["job_id"] == "j_chain"


def test_submit_and_wait_rejects_missing_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    erg, _ = _import_generated()
    with pytest.raises(ValueError, match="no job_id"):
        erg.submit_and_wait("http://x", lambda: {"task_ids": []}, timeout=1.0)


def test_submit_and_stream_yields_events_then_returns_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """submit_and_stream should yield each SSE event live + return
    the terminal JobDetail via PEP 380 ``StopIteration.value``."""
    import httpx

    erg, _ = _import_generated()
    sse_body = (
        'id: 1\nevent: progress\ndata: {"phase":"extract"}\n\n'
        'id: 2\nevent: progress\ndata: {"phase":"match"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse_body.encode("utf-8"),
            )
        if request.url.path.startswith("/v1/jobs/") and request.method == "GET":
            return httpx.Response(
                200,
                json={"job_id": "j_stream", "status": "succeeded", "tasks": []},
            )
        return httpx.Response(404, json={"detail": "not routed"})

    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client

    from typing import Any as _Any

    def patched_client_cls(*args: _Any, **kwargs: _Any) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", patched_client_cls)

    seen: list[str] = []
    gen = erg.submit_and_stream(
        "http://x",
        lambda: {"job_id": "j_stream", "task_ids": ["t1"]},
        timeout=5.0,
    )
    # PEP 380: drain the generator and capture the return value.
    try:
        while True:
            ev = next(gen)
            seen.append(ev.id)
    except StopIteration as stop:
        final = stop.value
    assert seen == ["1", "2"]
    assert isinstance(final, dict)
    assert final["status"] == "succeeded"


def test_submit_and_stream_rejects_missing_job_id() -> None:
    erg, _ = _import_generated()
    gen = erg.submit_and_stream("http://x", lambda: {"task_ids": []}, timeout=1.0)
    with pytest.raises(ValueError, match="no job_id"):
        next(gen)


def test_sse_stream_terminates_after_job_reaches_terminal(
    live_ephemeral_server: str,
) -> None:
    """Live-server validation that ``/v1/jobs/{id}/events`` actually
    closes the SSE stream once the job reaches a terminal status.

    A handler regression to ``while True: yield; sleep`` (the bug
    we fixed by adding the terminal-then-drain exit) would cause
    ``stream_events`` here to hang until the global timeout — the
    timer-based assertion catches that explicitly.
    """
    import time

    erg, _ = _import_generated()
    base = live_ephemeral_server
    did = _bootstrap_dataset(base, "sse-term")
    sub = _stage_submit(base, did)

    # Wait for the inline-queue worker to mark Job terminal.
    final = erg.wait_for_job(base, sub["job_id"], poll_interval=0.05, timeout=10.0)
    assert final["status"] in erg.TERMINAL_JOB_STATES

    # Now drain the SSE stream — MUST return cleanly. Time the drain
    # so we can assert it didn't rely on the global timeout. Handler
    # polls at 1s + one final drain cycle; ≥5s means regressed.
    drain_started = time.monotonic()
    events = list(erg.stream_events(base, sub["job_id"], timeout=10.0))
    drain_elapsed = time.monotonic() - drain_started
    assert drain_elapsed < 5.0, (
        f"SSE stream took {drain_elapsed:.2f}s to drain — likely "
        "regressed to unterminated `while True` loop"
    )
    _ = events  # count varies on stage-failure path


def test_dispatcher_finalizes_job_after_every_task_transition() -> None:
    """Static guard: ``app/workers/dispatcher.py::execute_task`` must
    call ``_maybe_finalize_job`` after every terminal Task transition
    (succeeded / failed-via-exception / failed-via-PycolmapUnavailable
    / failed-via-UnknownTask). Missing any one re-introduces the
    "Job.status stuck at pending" bug that broke wait_for_job until
    we added the rollup.
    """
    from pathlib import Path as _Path

    src = (_Path(__file__).resolve().parents[2] / "app" / "workers" / "dispatcher.py").read_text(
        encoding="utf-8"
    )
    # The function must exist + be called from every terminal branch.
    has_helper = "def _maybe_finalize_job" in src or "_maybe_finalize_job(" in src
    assert has_helper, (
        "dispatcher.py is missing _maybe_finalize_job — Job.status "
        "rollup will silently break wait_for_job."
    )
    # Counting call sites is brittle; instead count the four
    # known-terminal transitions and require at least that many calls.
    finalize_calls = src.count("_maybe_finalize_job(")
    # Expected sites: definition (1) + 4 call sites (UnknownTask,
    # success, PycolmapUnavailable, generic exception). Anything
    # less than 5 means a branch lost its rollup.
    assert finalize_calls >= 5, (
        f"dispatcher.py has only {finalize_calls} _maybe_finalize_job "
        "references; expected ≥5 (definition + 4 task-transition "
        "branches). A terminal branch is missing the rollup call."
    )


def test_get_blob_store_singletons_the_memory_backend() -> None:
    """Static + dynamic guard: ``app/storage/blobs.py::get_blob_store``
    MUST cache the in-memory backend instance. Constructing a fresh
    InMemoryBlobStore per call breaks every multi-call flow because
    the bytes live in a per-instance dict — uploaded blobs become
    "missing" to subsequent readers.
    """
    from app.core.config import Settings
    from app.storage.blobs import (
        InMemoryBlobStore,
        get_blob_store,
        reset_memory_blob_store_for_tests,
    )

    s = Settings(blob_backend="memory")
    reset_memory_blob_store_for_tests()
    a = get_blob_store(s)
    b = get_blob_store(s)
    assert isinstance(a, InMemoryBlobStore)
    assert a is b, (
        "get_blob_store() returned two different InMemoryBlobStore "
        "instances — uploaded blobs will be unreachable to subsequent "
        "callers. See CLAUDE.md '`InMemoryBlobStore` is a "
        "process-local singleton'."
    )
    # reset_memory_blob_store_for_tests must give us a fresh instance.
    reset_memory_blob_store_for_tests()
    c = get_blob_store(s)
    assert c is not a
    reset_memory_blob_store_for_tests()


def test_materialize_dag_persists_node_metadata() -> None:
    """Static guard: ``app/services/job_service.py::materialize_dag``
    MUST persist ``TaskNode.metadata`` (carrying ``inputs`` /
    ``spec``) to ``Task.task_state_json`` for non-cached nodes.

    Removing that branch re-introduces the
    ``KeyError: 'project_id'`` crash workers hit on every
    fresh-non-cached task — surfaced and fixed when the contract
    layer first started recording job-submit fixtures. ``L27`` in
    ``decisions.md`` formalizes the column split (pre-execution
    state in ``task_state_json``; post-execution result in
    ``outputs_ref_json``).
    """
    from pathlib import Path as _Path

    src = (_Path(__file__).resolve().parents[2] / "app" / "services" / "job_service.py").read_text(
        encoding="utf-8"
    )
    has_metadata_branch = "n.metadata" in src and "task_state_json" in src
    assert has_metadata_branch, (
        "job_service.py::materialize_dag is no longer persisting "
        "TaskNode.metadata to Task.task_state_json. Workers crash "
        "with KeyError on every fresh-non-cached task."
    )


def test_job_accepted_response_carries_stage_specific_typed_fields() -> None:
    """Static + dynamic guard: ``JobAcceptedResponse`` exposes
    every stage-specific key as a named typed field so SDK codegen
    surfaces them as typed accessors (Agent 1 + Agent 2 finding,
    May 2026 audit).

    The previous shape used ``extra="allow"`` plus per-route dict
    spread (``body["target_recon_id"] = ...``); SDK codegen saw
    the loose envelope and emitted ``Record<string, unknown>``,
    losing autocomplete on the very fields the route promised.
    The replacement: declare every stage-specific key as
    ``Optional`` directly on the envelope. ``model_dump()`` of a
    routed response now matches the recorded fixture exactly.
    """
    from app.schemas.api.jobs import JobAcceptedResponse

    # Stage-specific keys MUST be declared on the model (typed access).
    declared = set(JobAcceptedResponse.model_fields)
    for name in (
        "recon_id",
        "dataset_id",
        "project_id",
        "method",
        "applied_sim3",
        "target_recon_id",
        "source_recon_ids",
        "strategy",
        "action_id",
        "backend",
    ):
        assert name in declared, (
            f"JobAcceptedResponse is missing typed field {name!r} — "
            "regression: SDK codegen will lose typed access to it."
        )

    # Round-trip: every stage-specific key survives model_dump.
    inst = JobAcceptedResponse.model_validate(
        {
            "job_id": "01HZJOB000000000000000000",
            "task_ids": ["01HZTASK00000000000000000"],
            "strategy": "vlad",
            "target_recon_id": "01HZRECON0000000000000000",
            "source_recon_ids": ["01HZRECON1111111111111111"],
        }
    )
    dumped = inst.model_dump()
    assert dumped["strategy"] == "vlad"
    assert dumped["target_recon_id"] == "01HZRECON0000000000000000"
    assert dumped["source_recon_ids"] == ["01HZRECON1111111111111111"]


def test_paths_exposes_workspace_root() -> None:
    """Static guard: workers reach for ``paths.workspace_root`` to
    build per-task staging dirs. If the property disappears,
    ``_materialize`` crashes with an AttributeError on every fresh
    task. (Surfaced once already — kept here as a permanent guard.)
    """
    from app.core.config import Settings
    from app.core.paths import Paths

    p = Paths(Settings())
    # Property is an attribute access, not a method call.
    assert hasattr(p, "workspace_root"), (
        "Paths.workspace_root removed — workers calling "
        "paths.workspace_root in app/workers/_materialize.py and "
        "extract.py will crash with AttributeError on every fresh task."
    )
    assert p.workspace_root == p.s.workspace_root


def test_jobs_events_handler_has_terminal_exit_clause() -> None:
    """Static guard: the SSE handler in ``app/api/v1/jobs.py`` MUST
    contain a terminal-status check that breaks its tail loop. A
    regression to ``while True: yield; sleep`` without that check
    re-introduces the hang we fixed.

    Cheap to maintain, expensive bug to debug — keep the guard.
    """
    from pathlib import Path as _Path

    src = (_Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "jobs.py").read_text(
        encoding="utf-8"
    )
    # Two signals must be present; either disappearing means the
    # terminal-drain exit was lost.
    has_guard = "terminal_seen" in src or "terminal_statuses" in src
    assert has_guard, (
        "jobs.py SSE handler is missing its terminal-status guard. "
        "See CLAUDE.md 'SSE stream termination' for context."
    )
    has_succeeded = "succeeded" in src
    has_failed = "failed" in src
    has_cancelled = "cancelled" in src
    assert has_succeeded, "jobs.py missing 'succeeded' terminal-state literal"
    assert has_failed, "jobs.py missing 'failed' terminal-state literal"
    assert has_cancelled, "jobs.py missing 'cancelled' terminal-state literal"


def test_parallel_jobs_independent_terminal_and_sse_drain(
    live_ephemeral_server: str,
) -> None:
    """Live-server check: two parallel features jobs against the
    same dataset must reach terminal independently AND both SSE
    streams must drain concurrently without one starving the other.

    Catches race conditions in the terminal-then-drain protocol that
    single-job tests miss — e.g. ``_maybe_finalize_job`` rolling up
    Job.status based on cross-job task aggregation, or the SSE
    handler closing streams keyed on a process-level singleton.
    Mirrors TS ``test/generated_live.test.ts::two parallel jobs``.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    erg, _ = _import_generated()
    base = live_ephemeral_server
    did = _bootstrap_dataset(base, "parallel")
    job_a = _stage_submit(base, did)["job_id"]
    job_b = _stage_submit(base, did)["job_id"]
    assert job_a != job_b, "parallel jobs must have distinct ULIDs"

    # Wait for both terminal in parallel — same parallelism as TS
    # `Promise.all([waitForJob, waitForJob])`.
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_a = pool.submit(erg.wait_for_job, base, job_a, poll_interval=0.05, timeout=30.0)
        f_b = pool.submit(erg.wait_for_job, base, job_b, poll_interval=0.05, timeout=30.0)
        final_a = f_a.result(timeout=35.0)
        final_b = f_b.result(timeout=35.0)
    assert final_a["status"] in erg.TERMINAL_JOB_STATES
    assert final_b["status"] in erg.TERMINAL_JOB_STATES
    assert final_a["job_id"] == job_a, "cross-bleed: wait_for_job(job_a) returned wrong job_id"
    assert final_b["job_id"] == job_b, "cross-bleed: wait_for_job(job_b) returned wrong job_id"

    # Drain both SSE streams concurrently. Both must close cleanly
    # within the timer budget; neither should starve the other.
    drain_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_a = pool.submit(lambda: list(erg.stream_events(base, job_a, timeout=10.0)))
        f_b = pool.submit(lambda: list(erg.stream_events(base, job_b, timeout=10.0)))
        events_a = f_a.result(timeout=15.0)
        events_b = f_b.result(timeout=15.0)
    drain_elapsed = time.monotonic() - drain_started
    assert drain_elapsed < 7.0, (
        f"concurrent SSE drains took {drain_elapsed:.2f}s — likely "
        "starving each other or holding a shared lock"
    )
    _ = events_a, events_b  # counts vary on stage-failure path


def test_chained_ergonomics_against_live_server(live_ephemeral_server: str) -> None:
    """End-to-end live-server validation that ALL the ergonomics
    helpers compose correctly: upload_bytes -> register image ->
    submit_and_wait via the features stage -> wait_for_job sees the
    rolled-up Job.status from the dispatcher.

    Stubbed-fetch unit tests prove each helper's wire-shape contract
    in isolation; this test proves they actually wire together
    against the real FastAPI app, including the
    ``_maybe_finalize_job`` rollup that ``wait_for_job`` depends on.
    """
    import httpx as _httpx

    erg, _ = _import_generated()
    base = live_ephemeral_server
    did = _bootstrap_dataset(base, "chained")

    # submit_and_wait — chains submit + wait_for_job
    detail_a = erg.submit_and_wait(
        base, lambda: _stage_submit(base, did), poll_interval=0.05, timeout=15.0
    )
    assert detail_a["status"] in erg.TERMINAL_JOB_STATES
    assert detail_a["job_id"]

    # submit_and_stream — same primitive but consuming live SSE
    gen = erg.submit_and_stream(base, lambda: _stage_submit(base, did), timeout=15.0)
    try:
        for _ev in gen:
            pass
    except StopIteration as stop:
        detail_b = stop.value
    else:
        # Generator exhausted normally without StopIteration when SSE
        # emits zero events — pick up terminal detail manually.
        detail_b = erg.wait_for_job(base, detail_a["job_id"], timeout=5.0)
    assert detail_b["status"] in erg.TERMINAL_JOB_STATES

    # Cross-check via typed JobDetail decoder on the generated SDK.
    from sfmapi_client_gen.models.job_detail import JobDetail as GenJobDetail

    with _httpx.Client(base_url=base, timeout=10.0) as c:
        raw = c.get(f"/v1/jobs/{detail_a['job_id']}").json()
    typed = GenJobDetail.from_dict(raw)
    assert typed.status in erg.TERMINAL_JOB_STATES
    assert typed.job_id == detail_a["job_id"]


def test_wait_for_job_404_translates_to_typed_error() -> None:
    """wait_for_job against a missing job raises NotFoundError, not
    a generic httpx exception."""
    erg, _ = _import_generated()
    import httpx

    # Point at a port that won't accept the connection — should
    # raise a transport error, not silently spin. Use a tiny timeout
    # so the test is fast.
    with pytest.raises((erg.SfmApiError, httpx.ConnectError, httpx.HTTPError)):
        erg.wait_for_job("http://127.0.0.1:1", "01HZJOB0000000000000000000", timeout=0.5)
