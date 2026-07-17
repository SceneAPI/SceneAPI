"""Lease-reclaim janitor.

A task whose worker dies keeps its ``running`` status forever — the
heartbeat stops, the lease ages out, but nothing resets it. The janitor
sweeps for those expired leases. These tests pin the reclaim predicate
and the re-enqueue side effect.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from sceneapi.server.core.ids import new_id
from sceneapi.server.db.models import (
    Dataset,
    ImageSource,
    Job,
    JobEvent,
    Project,
    Reconstruction,
    RuntimeVersion,
    StageArtifact,
    Task,
    Upload,
)
from sceneapi.server.orchestrator.lease import now_utc

pytestmark = pytest.mark.integration


async def _seed_task(
    session,
    *,
    status: str,
    lease_offset_seconds: float | None,
) -> str:
    """Persist a Task with a given status and lease age.

    ``lease_offset_seconds`` is relative to now: negative = expired,
    positive = still valid, ``None`` = never leased.
    """
    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name=f"janp-{new_id()[:6]}")
    session.add(project)
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="x")
    session.add(job)
    await session.flush()
    lease = None
    worker = None
    if lease_offset_seconds is not None:
        lease = now_utc() + timedelta(seconds=lease_offset_seconds)
        worker = "worker-A"
    task = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        inputs_hash="x",
        params_hash="x",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
        status=status,
        worker_id=worker,
        lease_expires_at=lease,
    )
    session.add(task)
    await session.commit()
    return task.task_id


async def test_reclaim_resets_running_task_with_expired_lease(session) -> None:
    from sceneapi.server.orchestrator.janitor import reclaim_expired_leases

    task_id = await _seed_task(session, status="running", lease_offset_seconds=-60)

    reclaimed = await reclaim_expired_leases(session)
    assert reclaimed == [task_id]

    task = await session.get(Task, task_id)
    await session.refresh(task)
    assert task.status == "pending"
    assert task.worker_id is None
    assert task.lease_expires_at is None


async def test_reclaim_ignores_running_task_with_valid_lease(session) -> None:
    from sceneapi.server.orchestrator.janitor import reclaim_expired_leases

    task_id = await _seed_task(session, status="running", lease_offset_seconds=60)

    reclaimed = await reclaim_expired_leases(session)
    assert reclaimed == []

    task = await session.get(Task, task_id)
    await session.refresh(task)
    assert task.status == "running"
    assert task.worker_id == "worker-A"


async def test_reclaim_ignores_terminal_task_with_expired_lease(session) -> None:
    """A succeeded task with a stale lease must not be dragged back to
    pending — only ``running`` tasks are reclaimable."""
    from sceneapi.server.orchestrator.janitor import reclaim_expired_leases

    task_id = await _seed_task(session, status="succeeded", lease_offset_seconds=-60)

    reclaimed = await reclaim_expired_leases(session)
    assert reclaimed == []

    task = await session.get(Task, task_id)
    await session.refresh(task)
    assert task.status == "succeeded"


async def test_ready_pending_tasks_skip_unmaterialized_task_state(session) -> None:
    from sceneapi.server.orchestrator.janitor import find_ready_pending_tasks

    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name=f"ready-{new_id()[:6]}")
    session.add(project)
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="x")
    session.add(job)
    await session.flush()
    task = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        inputs_hash="x",
        params_hash="x",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
        status="pending",
        task_state_json=None,
    )
    session.add(task)
    await session.commit()

    assert await find_ready_pending_tasks(session) == []


async def test_ready_pending_tasks_treat_skipped_dependencies_as_done(session) -> None:
    from sceneapi.server.orchestrator.janitor import find_ready_pending_tasks

    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name=f"ready-{new_id()[:6]}")
    session.add(project)
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="x")
    session.add(job)
    await session.flush()
    upstream = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        inputs_hash="u",
        params_hash="u",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
        status="skipped",
        task_state_json={"inputs": {}, "spec": {}},
    )
    downstream = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        inputs_hash="d",
        params_hash="d",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
        status="pending",
        depends_on_json=[upstream.task_id],
        task_state_json={"inputs": {}, "spec": {}},
    )
    session.add_all([upstream, downstream])
    await session.commit()

    assert await find_ready_pending_tasks(session) == [downstream.task_id]


async def test_run_janitor_once_re_enqueues_reclaimed_task(session) -> None:
    """``run_janitor_once`` resets the lease AND re-enqueues — under the
    inline queue the noop task runs and reaches ``succeeded``."""
    from sceneapi.server.orchestrator.janitor import run_janitor_once

    task_id = await _seed_task(session, status="running", lease_offset_seconds=-60)

    reclaimed = await run_janitor_once(session)
    assert reclaimed == [task_id]

    task = await session.get(Task, task_id)
    await session.refresh(task)
    # InlineQueue.enqueue runs the task synchronously to a terminal state.
    assert task.status == "succeeded"


async def test_run_janitor_once_re_enqueues_ready_pending_task(session) -> None:
    """A dependency-ready pending task can be stranded if submit-time enqueue
    failed. The janitor sweep retries it even though no lease expired."""
    from sceneapi.server.orchestrator.janitor import run_janitor_once

    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv-ready", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name=f"janp-{new_id()[:6]}")
    session.add(project)
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="noop-dag")
    session.add(job)
    await session.flush()
    first_id = new_id()
    second_id = new_id()
    session.add_all(
        [
            Task(
                task_id=first_id,
                tenant_id="default",
                job_id=job.job_id,
                kind="noop",
                inputs_hash="i1",
                params_hash="p1",
                runtime_version_id=rv.rv_id,
                cache_key=new_id(),
                gpu_required=False,
                status="succeeded",
                outputs_ref_json={"ok": True},
                finished_at=now_utc(),
            ),
            Task(
                task_id=second_id,
                tenant_id="default",
                job_id=job.job_id,
                kind="noop",
                inputs_hash="i2",
                params_hash="p2",
                runtime_version_id=rv.rv_id,
                cache_key=new_id(),
                gpu_required=False,
                status="pending",
                depends_on_json=[first_id],
                task_state_json={"inputs": {}, "spec": {}},
            ),
        ]
    )
    await session.commit()

    reclaimed = await run_janitor_once(session)
    assert reclaimed == []

    task = await session.get(Task, second_id)
    await session.refresh(task)
    await session.refresh(job)
    assert task.status == "succeeded"
    assert job.status == "succeeded"


async def test_run_janitor_once_marks_stranded_dependency_failures(session) -> None:
    """If a worker dies after committing an upstream terminal state but before
    DAG advancement, the janitor propagates that terminal dependency state."""
    from sceneapi.server.orchestrator.janitor import run_janitor_once

    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv-deps", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name=f"janp-{new_id()[:6]}")
    session.add(project)
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="noop-dag")
    session.add(job)
    await session.flush()
    first_id = new_id()
    second_id = new_id()
    third_id = new_id()
    session.add_all(
        [
            Task(
                task_id=first_id,
                tenant_id="default",
                job_id=job.job_id,
                kind="noop",
                inputs_hash="i1",
                params_hash="p1",
                runtime_version_id=rv.rv_id,
                cache_key=new_id(),
                gpu_required=False,
                status="failed",
                error_class="RuntimeError",
                error_message="boom",
                finished_at=now_utc(),
            ),
            Task(
                task_id=second_id,
                tenant_id="default",
                job_id=job.job_id,
                kind="noop",
                inputs_hash="i2",
                params_hash="p2",
                runtime_version_id=rv.rv_id,
                cache_key=new_id(),
                gpu_required=False,
                status="pending",
                depends_on_json=[first_id],
            ),
            Task(
                task_id=third_id,
                tenant_id="default",
                job_id=job.job_id,
                kind="noop",
                inputs_hash="i3",
                params_hash="p3",
                runtime_version_id=rv.rv_id,
                cache_key=new_id(),
                gpu_required=False,
                status="pending",
                depends_on_json=[second_id],
            ),
        ]
    )
    await session.commit()

    reclaimed = await run_janitor_once(session)
    assert reclaimed == []

    task = await session.get(Task, second_id)
    third = await session.get(Task, third_id)
    await session.refresh(task)
    await session.refresh(third)
    await session.refresh(job)
    assert task.status == "failed"
    assert task.error_class == "DependencyFailed"
    assert task.finished_at is not None
    assert third.status == "failed"
    assert third.error_class == "DependencyFailed"
    assert third.finished_at is not None
    assert job.status == "failed"


async def test_run_janitor_once_fails_reconstruction_for_stranded_map(session) -> None:
    from sceneapi.server.orchestrator.janitor import run_janitor_once

    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv-recon-deps", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name=f"janp-{new_id()[:6]}")
    session.add(project)
    await session.flush()
    source = ImageSource(tenant_id="default", kind="upload", fingerprint_json={})
    session.add(source)
    await session.flush()
    dataset = Dataset(
        tenant_id="default",
        project_id=project.project_id,
        source_id=source.source_id,
        name=f"jan-ds-{new_id()[:6]}",
        manifest_hash="0" * 64,
    )
    session.add(dataset)
    await session.flush()
    recon = Reconstruction(
        tenant_id="default",
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        dataset_snapshot_hash=dataset.manifest_hash,
        spec_json={"kind": "incremental", "version": 1},
        rv_id=rv.rv_id,
        status="running",
    )
    session.add(recon)
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="incremental")
    session.add(job)
    await session.flush()
    verify_id = new_id()
    map_id = new_id()
    session.add_all(
        [
            Task(
                task_id=verify_id,
                tenant_id="default",
                job_id=job.job_id,
                kind="verify",
                inputs_hash="verify-i",
                params_hash="verify-p",
                runtime_version_id=rv.rv_id,
                cache_key=new_id(),
                gpu_required=False,
                status="failed",
                error_class="RuntimeError",
                error_message="verify failed",
                finished_at=now_utc(),
                task_state_json={
                    "inputs": {"recon_id": recon.recon_id},
                    "spec": {},
                },
            ),
            Task(
                task_id=map_id,
                tenant_id="default",
                job_id=job.job_id,
                kind="map",
                inputs_hash="map-i",
                params_hash="map-p",
                runtime_version_id=rv.rv_id,
                cache_key=new_id(),
                gpu_required=False,
                status="pending",
                depends_on_json=[verify_id],
                task_state_json={
                    "inputs": {"recon_id": recon.recon_id},
                    "spec": {"kind": "incremental", "version": 1},
                },
            ),
        ]
    )
    await session.commit()

    assert await run_janitor_once(session) == []

    task = await session.get(Task, map_id)
    await session.refresh(task)
    await session.refresh(job)
    await session.refresh(recon)
    assert task.status == "failed"
    assert task.error_class == "DependencyFailed"
    assert job.status == "failed"
    assert recon.status == "failed"


async def test_run_janitor_once_marks_missing_dependencies_failed(session) -> None:
    from sceneapi.server.orchestrator.janitor import run_janitor_once

    rv = RuntimeVersion(rv_id=new_id(), runtime_version_id="test-rv-missing-dep", seed="0")
    session.add(rv)
    project = Project(tenant_id="default", name=f"janp-{new_id()[:6]}")
    session.add(project)
    await session.flush()
    job = Job(tenant_id="default", project_id=project.project_id, recipe="noop-dag")
    session.add(job)
    await session.flush()
    task = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        inputs_hash="i",
        params_hash="p",
        runtime_version_id=rv.rv_id,
        cache_key=new_id(),
        gpu_required=False,
        status="pending",
        depends_on_json=["01H00000000000000000000000"],
    )
    session.add(task)
    await session.commit()

    assert await run_janitor_once(session) == []

    await session.refresh(task)
    await session.refresh(job)
    assert task.status == "failed"
    assert task.error_class == "DependencyFailed"
    assert task.error_message == "upstream dependency missing"
    assert job.status == "failed"


async def _seed_upload(session, *, state: str, expires_offset_seconds: float) -> str:
    upload = Upload(
        upload_id=new_id(),
        tenant_id="default",
        expected_size=1024,
        received_bytes=0,
        state=state,
        expires_at=now_utc() + timedelta(seconds=expires_offset_seconds),
    )
    session.add(upload)
    await session.commit()
    return upload.upload_id


async def test_run_janitor_once_reaps_expired_unfinalized_upload(session) -> None:
    """The janitor sweep also drops uploads past expires_at that were
    never finalized — this is what backs the UploadState 'expired ...
    reaped by the janitor' doc claim."""
    from sceneapi.server.orchestrator.janitor import run_janitor_once

    stale = await _seed_upload(session, state="open", expires_offset_seconds=-3600)
    fresh = await _seed_upload(session, state="open", expires_offset_seconds=3600)
    finalized = await _seed_upload(session, state="finalized", expires_offset_seconds=-3600)

    await run_janitor_once(session)

    assert await session.get(Upload, stale) is None
    assert await session.get(Upload, fresh) is not None
    # A finalized upload past expires_at is kept — its bytes are content
    # addressed and may still be referenced.
    assert await session.get(Upload, finalized) is not None


# ----------------------------------------------------------------------
# Retention GC (settings-driven, default OFF)
# ----------------------------------------------------------------------


async def _seed_terminal_job(
    session,
    *,
    status: str = "succeeded",
    finished_days_ago: float | None = 40.0,
    pinned: bool = False,
    with_records: bool = False,
) -> tuple[str, Path]:
    """Persist a Job (+ one Task, optionally artifact/event rows and an
    events.jsonl file) finished ``finished_days_ago`` days in the past.
    Returns ``(job_id, events_jsonl_path)``."""
    from sceneapi.server.core.config import get_settings
    from sceneapi.server.core.paths import Paths

    # NB: ULID prefixes are time-ordered — same-millisecond ids share
    # their first chars, so use the random suffix for uniqueness.
    project = Project(tenant_id="default", name=f"gc-{new_id()[-10:]}")
    session.add(project)
    await session.flush()
    finished_at = None
    if finished_days_ago is not None:
        finished_at = now_utc() - timedelta(days=finished_days_ago)
    job = Job(
        tenant_id="default",
        project_id=project.project_id,
        recipe="x",
        status=status,
        pinned=pinned,
        finished_at=finished_at,
    )
    session.add(job)
    await session.flush()
    task = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        inputs_hash="x",
        params_hash="x",
        runtime_version_id="rv",
        cache_key=new_id(),
        gpu_required=False,
        status="succeeded",
        finished_at=finished_at,
    )
    session.add(task)
    if with_records:
        session.add(
            StageArtifact(
                tenant_id="default",
                job_id=job.job_id,
                task_id=task.task_id,
                kind="features.local.v1",
                uri="database.db",
            )
        )
        session.add(JobEvent(event_id=1, job_id=job.job_id, ts=now_utc(), payload_json={"k": 1}))
    events_file = (
        Paths(get_settings()).job_root("default", project.project_id, job.job_id) / "events.jsonl"
    )
    if with_records:
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.write_text('{"kind":"log"}\n', encoding="utf-8")
    await session.commit()
    return job.job_id, events_file


async def test_gc_expired_job_records_disabled_by_default(session) -> None:
    """``retention_days`` defaults to None — the sweep must be a no-op."""
    from sceneapi.server.orchestrator.janitor import gc_expired_job_records

    job_id, _ = await _seed_terminal_job(session, finished_days_ago=365)

    assert await gc_expired_job_records(session) == 0
    assert await session.get(Job, job_id) is not None


async def test_gc_expired_job_records_deletes_old_terminal_job(session) -> None:
    """An old terminal job loses its row, task/artifact/event rows, and
    its events.jsonl file."""
    from sceneapi.server.core.config import reset_settings_for_tests
    from sceneapi.server.orchestrator.janitor import gc_expired_job_records

    reset_settings_for_tests(retention_days=30)
    job_id, events_file = await _seed_terminal_job(session, finished_days_ago=31, with_records=True)
    assert events_file.is_file()

    removed = await gc_expired_job_records(session)
    await session.commit()

    assert removed == 1
    assert await session.get(Job, job_id) is None
    for model in (Task, StageArtifact, JobEvent):
        rows = (await session.execute(select(model).where(model.job_id == job_id))).scalars().all()
        assert rows == []
    assert not events_file.exists()


async def test_gc_expired_job_records_keeps_recent_pinned_and_unfinished_jobs(session) -> None:
    from sceneapi.server.core.config import reset_settings_for_tests
    from sceneapi.server.orchestrator.janitor import gc_expired_job_records

    reset_settings_for_tests(retention_days=30)
    recent_id, _ = await _seed_terminal_job(session, finished_days_ago=1)
    pinned_id, _ = await _seed_terminal_job(session, finished_days_ago=365, pinned=True)
    running_id, _ = await _seed_terminal_job(session, status="running", finished_days_ago=None)

    removed = await gc_expired_job_records(session)
    await session.commit()

    assert removed == 0
    assert await session.get(Job, recent_id) is not None
    assert await session.get(Job, pinned_id) is not None
    assert await session.get(Job, running_id) is not None


async def test_run_janitor_once_runs_retention_gc_when_enabled(session) -> None:
    """The GC stage is wired into the sweep behind the setting."""
    from sceneapi.server.core.config import reset_settings_for_tests
    from sceneapi.server.orchestrator.janitor import run_janitor_once

    reset_settings_for_tests(retention_days=30)
    old_id, _ = await _seed_terminal_job(session, finished_days_ago=31)
    fresh_id, _ = await _seed_terminal_job(session, finished_days_ago=1)

    await run_janitor_once(session)

    assert await session.get(Job, old_id) is None
    assert await session.get(Job, fresh_id) is not None
