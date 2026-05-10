"""Worker dispatcher (queue-agnostic execute_task)."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.core.ids import new_id
from app.db.models import StageArtifact, Task
from app.services import artifact_service, job_service, project_service
from app.workers.dispatcher import get_handlers
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
        "vlad_index",
        "localize",
        "georegister",
        "to_cubemap",
        "render_cubemap",
        "merge_recons",
        "video_frames",
        "kapture_import",
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


def test_worker_output_contract_infers_legacy_artifacts() -> None:
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
            "two_view_geometries_path": "two_view_geometries.json",
        },
    )

    assert [artifact["kind"] for artifact in out["artifacts"]] == [
        "matches.verified_database",
        "matches.two_view_geometries",
    ]
    assert out["artifacts"][0]["summary"] is not out
    assert "artifacts" not in out["artifacts"][0]["summary"]


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
    task = Task(
        task_id=new_id(),
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
        kind="features.database",
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
