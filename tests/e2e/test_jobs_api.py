"""Job + SSE endpoints — Phase 1.

The stage endpoints derive the image source + database path from the
dataset itself (per the v1 cleanup), so each test that submits a
features/match/verify job has to register at least one image first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


async def _upload(client, payload: bytes) -> str:
    init = await client.post("/v1/uploads", json={"expected_size": len(payload)})
    upload_id = init.json()["upload_id"]
    await client.patch(
        f"/v1/uploads/{upload_id}",
        content=payload,
        headers={"Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}"},
    )
    fin = await client.post(f"/v1/uploads/{upload_id}:finalize")
    return fin.json()["blob_sha"]


async def _project_with_image(client, name: str) -> tuple[str, str]:
    pr = await client.post("/v1/projects", json={"name": name})
    pid = pr.json()["project_id"]
    sha = await _upload(client, b"\xff\xd8\xff\xe0imagebytes")
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={
            "name": "ds",
            "source": {"kind": "upload", "entries": [{"name": "a.jpg", "blob_sha": sha}]},
        },
    )
    did = ds.json()["dataset_id"]
    await client.post(f"/v1/datasets/{did}/images", json={"name": "a.jpg", "blob_sha": sha})
    return pid, did


async def test_features_returns_202(client) -> None:
    _, did = await _project_with_image(client, "p-feat")
    resp = await client.post(
        f"/v1/datasets/{did}/features",
        json={"spec": {"max_num_features": 4096, "use_gpu": False}},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "job_id" in body
    assert "task_ids" in body
    job_id = body["job_id"]
    detail = await client.get(f"/v1/jobs/{job_id}")
    assert detail.status_code == 200
    j = detail.json()
    assert j["recipe"] == "features"
    assert len(j["tasks"]) == 1

    progress = await client.get(f"/v1/jobs/{job_id}/progress")
    assert progress.status_code == 200
    body = progress.json()
    assert body["job_id"] == job_id
    assert body["recipe"] == "features"
    assert body["total_tasks"] == 1
    assert len(body["tasks"]) == 1
    assert 0.0 <= body["progress"] <= 1.0


async def test_features_rejects_empty_dataset(client) -> None:
    """A dataset with no images can't be featured — the API says so up
    front rather than letting the job fail mid-flight."""
    pr = await client.post("/v1/projects", json={"name": "p-empty"})
    pid = pr.json()["project_id"]
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "ds", "source": {"kind": "upload", "entries": []}},
    )
    did = ds.json()["dataset_id"]
    resp = await client.post(f"/v1/datasets/{did}/features", json={"spec": {}})
    assert resp.status_code == 422
    assert "images" in resp.text.lower()


async def test_cancel_sets_flag(client) -> None:
    _, did = await _project_with_image(client, "p-cancel")
    resp = await client.post(f"/v1/datasets/{did}/features", json={"spec": {"use_gpu": False}})
    job_id = resp.json()["job_id"]
    cancel = await client.post(f"/v1/jobs/{job_id}:cancel")
    assert cancel.status_code == 200
    assert cancel.json()["cancel_requested"] is True

    cancel2 = await client.post(f"/v1/jobs/{job_id}:cancel?force=true")
    assert cancel2.json()["cancel_force"] is True


async def test_matches_requires_vocab_tree_for_vocabtree_strategy(client) -> None:
    _, did = await _project_with_image(client, "p-match")
    resp = await client.post(
        f"/v1/datasets/{did}/matches",
        json={"pairs": {"strategy": "vocabtree"}},
    )
    assert resp.status_code == 422


async def test_matches_requires_pair_source_for_explicit_strategy(client) -> None:
    _, did = await _project_with_image(client, "p-match-explicit-source")
    resp = await client.post(
        f"/v1/datasets/{did}/matches",
        json={"pairs": {"strategy": "explicit"}},
    )
    assert resp.status_code == 422


async def test_matches_rejects_unknown_explicit_pair_images(client) -> None:
    _, did = await _project_with_image(client, "p-match-explicit-unknown")
    resp = await client.post(
        f"/v1/datasets/{did}/matches",
        json={
            "pairs": {
                "strategy": "explicit",
                "image_pairs": [{"image_name1": "a.jpg", "image_name2": "missing.jpg"}],
            }
        },
    )
    assert resp.status_code == 422


async def test_verify_returns_202(client) -> None:
    _, did = await _project_with_image(client, "p-verify")
    resp = await client.post(f"/v1/datasets/{did}/verify", json={"spec": {}})
    assert resp.status_code == 202


async def test_progress_snapshot_uses_latest_phase_event(client, session) -> None:
    from sfmapi.server.db.models import Job, JobEvent, Project, Task

    now = datetime.now(UTC)
    project = Project(tenant_id="default", name="p-progress")
    session.add(project)
    await session.flush()
    job = Job(
        tenant_id="default",
        project_id=project.project_id,
        recipe="global",
        status="running",
        spec_json={},
    )
    session.add(job)
    await session.flush()
    task = Task(
        tenant_id="default",
        job_id=job.job_id,
        kind="match",
        inputs_hash="inputs",
        params_hash="params",
        runtime_version_id="rv",
        cache_key="cache",
        status="running",
        started_at=now,
    )
    session.add(task)
    await session.flush()
    session.add(
        JobEvent(
            event_id=1,
            job_id=job.job_id,
            ts=now,
            payload_json={
                "schema_version": 1,
                "kind": "phase_progress",
                "ts": now.isoformat(),
                "job_id": job.job_id,
                "task_id": task.task_id,
                "seq": 1,
                "phase": "matching",
                "current": 25,
                "total": 100,
            },
        )
    )
    await session.commit()

    response = await client.get(f"/v1/jobs/{job.job_id}/progress")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["progress"] == 0.25
    assert body["current_task_id"] == task.task_id
    assert body["current_phase"] == "matching"
    assert body["latest_event"]["kind"] == "phase_progress"
    assert body["tasks"][0]["progress"] == 0.25
    assert body["tasks"][0]["current"] == 25
    assert body["tasks"][0]["total"] == 100


async def test_backend_progress_reporter_events_are_persisted(client, session) -> None:
    from sqlalchemy import select

    from sfmapi.server.adapters.registry import register_backend
    from sfmapi.server.adapters.stub_backend import StubBackend
    from sfmapi.server.db.models import JobEvent

    class ReportingBackend(StubBackend):
        def extract_features(
            self,
            *,
            database_path: Path,
            image_root: Path,
            image_list: list[str],
            options: dict,
            progress=None,
        ) -> dict:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_path.touch()
            if progress is not None:
                progress.phase_progress(
                    "feature_extraction",
                    current=1,
                    total=max(1, len(image_list)),
                )
            return {"num_images": len(image_list), "num_keypoints": 12}

    register_backend("stub", ReportingBackend)

    _, did = await _project_with_image(client, "p-progress-backend")
    response = await client.post(f"/v1/datasets/{did}/features", json={"spec": {}})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    rows = (
        (await session.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
    )
    payloads = [row.payload_json for row in rows]
    assert any(
        payload.get("kind") == "phase_progress"
        and payload.get("phase") == "feature_extraction"
        and payload.get("current") == 1
        for payload in payloads
    )

    progress = await client.get(f"/v1/jobs/{job_id}/progress")
    assert progress.status_code == 200
    body = progress.json()
    assert body["status"] == "succeeded"
    assert body["progress"] == 1.0
    assert body["latest_event_id"] is not None


async def test_matches_can_select_feature_artifact_as_input(client) -> None:
    from sfmapi.server.adapters.registry import register_backend
    from sfmapi.server.adapters.stub_backend import StubBackend

    captured: dict[str, object] = {}

    class ArtifactAwareBackend(StubBackend):
        def extract_features(
            self,
            *,
            database_path: Path,
            image_root: Path,
            image_list: list[str],
            options: dict,
            progress=None,
        ) -> dict:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_path.write_text("feature-db", encoding="utf-8")
            return {"num_images": len(image_list), "num_keypoints": 12}

        def match(self, *, database_path: Path, mode: str, options: dict) -> dict:
            captured["database_path"] = str(database_path)
            captured["mode"] = mode
            captured["input_artifacts"] = options.get("input_artifacts")
            return {"num_pairs": 1, "num_matches": 2}

    register_backend("stub", ArtifactAwareBackend)

    _, did = await _project_with_image(client, "p-artifact-input-flow")
    features = await client.post(f"/v1/datasets/{did}/features", json={"spec": {}})
    assert features.status_code == 202, features.text
    feature_job_id = features.json()["job_id"]

    artifacts = await client.get(
        f"/v1/jobs/{feature_job_id}/artifacts",
        params={"kind": "features.database.stub"},
    )
    assert artifacts.status_code == 200, artifacts.text
    feature_artifact = artifacts.json()["items"][0]

    matches = await client.post(
        f"/v1/datasets/{did}/matches",
        json={
            "pairs": {"strategy": "exhaustive"},
            "matcher": {"type": "nn-mutual"},
            "input_artifacts": {
                "features": {
                    "artifact_id": feature_artifact["artifact_id"],
                    "kind": "features.database.stub",
                }
            },
        },
    )
    assert matches.status_code == 202, matches.text
    assert feature_artifact["uri"] == f"/v1/artifacts/{feature_artifact['artifact_id']}/content"
    assert Path(captured["database_path"]).read_text(encoding="utf-8") == "feature-db"
    assert captured["mode"] == "exhaustive"
    selected = captured["input_artifacts"]
    assert isinstance(selected, dict)
    assert selected["features"]["artifact_id"] == feature_artifact["artifact_id"]
