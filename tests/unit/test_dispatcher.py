"""Worker dispatcher (queue-agnostic execute_task)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.ids import new_id
from app.db.models import Dataset, Image, ImageSource, StageArtifact, Task
from app.services import artifact_service, job_service, project_service
from app.workers.dispatcher import (
    _apply_derived_dataset_outputs,
    _dependency_state_from_statuses,
    execute_task,
    get_handlers,
)
from app.workers.runner import WORKER_ID, run_task

pytestmark = pytest.mark.unit


def test_handler_registry_has_all_known_kinds() -> None:
    handlers = get_handlers()
    expected = {
        "noop",
        "extract",
        "match",
        "verify",
        "map",
        "ba",
        "triangulate",
        "relocalize",
        "pgo",
        "export",
        "undistort",
        "vocab_tree",
        "configure_rig",
        "two_view",
        "vlad_index",
        "localize",
        "georegister",
        "to_cubemap",
        "project_images",
        "convert_artifact",
        "merge_recons",
        "video_frames",
        "kapture_import",
        "import_archive",
    }
    assert expected.issubset(handlers.keys())


def test_runner_run_task_delegates_to_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ARQ shim in runner.py must call dispatcher.execute_task —
    if it ever stops doing that, the queue plugins won't actually run
    anything."""
    seen: list[str] = []

    async def fake_execute(task_id: str) -> dict:
        seen.append(task_id)
        return {"status": "fake"}

    import app.workers.runner as runner_mod

    monkeypatch.setattr(runner_mod, "execute_task", fake_execute)

    import asyncio

    result = asyncio.run(run_task({"unused": "ctx"}, "t_dispatcher_test"))
    assert seen == ["t_dispatcher_test"]
    assert result == {"status": "fake"}


def test_worker_id_is_set() -> None:
    assert WORKER_ID
    assert isinstance(WORKER_ID, str)


def test_skipped_dependency_counts_as_ready() -> None:
    assert (
        _dependency_state_from_statuses(
            ["a", "b"],
            {"a": "succeeded", "b": "skipped"},
        )
        == "ready"
    )


def test_worker_output_contract_rejects_malformed_artifacts() -> None:
    task = Task(
        task_id="01H00000000000000000000000",
        tenant_id="default",
        job_id="01H00000000000000000000001",
        kind="match",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        task_state_json={"inputs": {"recon_id": "r1", "dataset_id": "d1"}},
    )

    with pytest.raises(ValidationError, match=r"outputs.artifacts\[0\].kind"):
        artifact_service.normalize_task_outputs(
            task,
            {"artifacts": [{"kind": "bad kind with spaces"}]},
        )


def test_worker_output_contract_requires_explicit_artifacts() -> None:
    task = Task(
        task_id="01H00000000000000000000000",
        tenant_id="default",
        job_id="01H00000000000000000000001",
        kind="verify",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        task_state_json={"inputs": {"recon_id": "r1", "dataset_id": "d1"}},
    )

    out = artifact_service.normalize_task_outputs(
        task,
        {
            "database_path": "database.db",
            "artifacts": [
                {
                    "kind": "matches.verified.v1",
                    "name": "two_view_geometries",
                    "uri": "two_view_geometries.json",
                    "artifact_format": "sfmapi.matches.verified.v1",
                    "schema_version": 1,
                }
            ],
        },
    )

    assert [artifact["kind"] for artifact in out["artifacts"]] == ["matches.verified.v1"]
    assert out["artifacts"][0]["metadata"]["artifact_format"] == "sfmapi.matches.verified.v1"
    assert out["artifacts"][0]["metadata"]["datatype"] == "match_graph"


def test_worker_output_contract_rejects_incompatible_core_format() -> None:
    task = Task(
        task_id="01H00000000000000000000000",
        tenant_id="default",
        job_id="01H00000000000000000000001",
        kind="verify",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
    )

    with pytest.raises(ValidationError, match="not compatible"):
        artifact_service.normalize_task_outputs(
            task,
            {
                "artifacts": [
                    {
                        "kind": "matches.verified.v1",
                        "artifact_format": "sfmapi.features.local.v1",
                    }
                ]
            },
        )


async def test_input_artifact_refs_validate_role_and_kind(session) -> None:
    project = await project_service.create_project(
        session,
        tenant_id="default",
        name="artifact-inputs",
    )
    job = await job_service.create_job(
        session,
        tenant_id="default",
        project_id=project.project_id,
        recipe="test",
        spec={},
    )
    task_id = new_id()
    task = Task(
        task_id=task_id,
        tenant_id="default",
        job_id=job.job_id,
        kind="extract",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
    )
    session.add(task)
    await session.flush()
    artifact = StageArtifact(
        tenant_id="default",
        job_id=job.job_id,
        task_id=task.task_id,
        kind="features.local.v1",
        uri="database.db",
    )
    session.add(artifact)
    await session.flush()

    resolved = await artifact_service.resolve_input_artifacts(
        session,
        tenant_id="default",
        dataset_id=None,
        input_artifacts={"features": {"artifact_id": artifact.artifact_id}},
    )
    assert resolved["features"]["uri"] == "database.db"

    with pytest.raises(ValidationError, match="expects one of"):
        await artifact_service.resolve_input_artifacts(
            session,
            tenant_id="default",
            dataset_id=None,
            input_artifacts={"verified_matches": {"artifact_id": artifact.artifact_id}},
        )


async def test_execute_task_does_not_rerun_terminal_task(session, monkeypatch) -> None:
    project = await project_service.create_project(
        session,
        tenant_id="default",
        name="terminal-task",
    )
    job = await job_service.create_job(
        session,
        tenant_id="default",
        project_id=project.project_id,
        recipe="noop",
        spec={},
    )
    task_id = new_id()
    task = Task(
        task_id=task_id,
        tenant_id="default",
        job_id=job.job_id,
        kind="noop",
        status="succeeded",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        outputs_ref_json={"ok": True},
    )
    session.add(task)
    await session.commit()

    calls = 0

    def fail_if_called(_task: Task) -> dict:
        nonlocal calls
        calls += 1
        raise AssertionError("terminal task should not be executed again")

    import app.workers.dispatcher as dispatcher

    monkeypatch.setattr(dispatcher, "_HANDLERS_CACHE", {"noop": fail_if_called})

    result = await execute_task(task_id)

    assert result == {"status": "succeeded"}
    assert calls == 0


def test_database_path_routing_only_uses_backend_database_artifacts() -> None:
    input_artifacts = {
        "features": {
            "artifact_id": "portable-features",
            "kind": "features.local.v1",
            "uri": "features.json",
        },
        "matches": {
            "artifact_id": "portable-matches",
            "kind": "matches.indexed.v1",
            "uri": "matches.json",
        },
        "verified_matches": {
            "artifact_id": "verified-db",
            "kind": "matches.database.verified.colmap",
            "uri": "database.db",
        },
    }

    assert (
        artifact_service.database_path_from_input_artifacts(
            input_artifacts,
            roles=("features", "matches"),
        )
        is None
    )
    assert (
        artifact_service.database_path_from_input_artifacts(
            input_artifacts,
            roles=("verified_matches",),
        )
        == "database.db"
    )


async def test_derived_dataset_registration_is_collision_safe_and_idempotent(session) -> None:
    project = await project_service.create_project(
        session,
        tenant_id="default",
        name="derived-datasets",
    )
    job = await job_service.create_job(
        session,
        tenant_id="default",
        project_id=project.project_id,
        recipe="project_images",
        spec={},
    )
    task = Task(
        task_id=new_id(),
        tenant_id="default",
        job_id=job.job_id,
        kind="project_images",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
    )
    session.add(task)
    await session.flush()

    source = ImageSource(
        tenant_id="default",
        kind="local",
        uri_or_root=None,
        fingerprint_json={"kind": "test"},
    )
    session.add(source)
    await session.flush()
    existing = Dataset(
        tenant_id="default",
        project_id=project.project_id,
        source_id=source.source_id,
        name="cube",
        camera_model="SPHERICAL",
        intrinsics_mode="single_camera",
        manifest_hash="",
    )
    session.add(existing)
    await session.flush()

    root = get_settings().workspace_root / "derived-test"
    root.mkdir(parents=True)
    (root / "front.jpg").write_bytes(b"fake image bytes")
    outputs = {
        "derived_dataset": {
            "name": "cube",
            "root": str(root),
            "camera_model": "PINHOLE",
            "intrinsics_mode": "per_image",
            "images": [{"name": "front.jpg", "width": 32, "height": 32}],
        }
    }

    await _apply_derived_dataset_outputs(session, task, outputs)
    first_dataset_id = outputs["derived_dataset"]["dataset_id"]
    assert outputs["derived_dataset"]["name"].startswith("cube-")

    await _apply_derived_dataset_outputs(session, task, outputs)

    datasets = (
        (
            await session.execute(
                select(Dataset).where(
                    Dataset.project_id == project.project_id,
                    Dataset.name.like("cube-%"),
                )
            )
        )
        .scalars()
        .all()
    )
    images = (
        (await session.execute(select(Image).where(Image.dataset_id == first_dataset_id)))
        .scalars()
        .all()
    )
    assert len(datasets) == 1
    assert len(images) == 1
    assert outputs["derived_dataset"]["dataset_id"] == first_dataset_id
    assert outputs["derived_dataset"]["reused"] is True
