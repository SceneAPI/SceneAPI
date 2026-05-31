"""End-to-end coverage for provider-aware backend routing.

Provider routing only matters if a ``provider`` value posted to an
HTTP route actually lands in the worker handler. These tests pin the
HTTP → service → ``Task.task_state_json`` hand-off so a regression
that silently strips ``provider`` from the persisted spec is caught.

They also pin the RFC 7807 wire shape for
``ProviderAmbiguityError`` so the documented ``candidates`` /
``suggested_fix`` extras stay in the response envelope.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration


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


async def _project_with_image(client, name: str = "p-provider") -> tuple[str, str]:
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


async def _read_task_spec(session_factory, task_id: str) -> dict[str, Any]:
    """Read the persisted ``task_state_json['spec']`` from the DB."""
    from app.db.models import Task

    async with session_factory() as session:
        row = await session.execute(select(Task).where(Task.task_id == task_id))
        task = row.scalar_one()
        state = task.task_state_json or {}
        return state.get("spec") or {}


async def test_features_post_persists_provider_into_task_spec(client) -> None:
    """A ``provider`` posted on the features stage must arrive in the
    worker's ``Task.task_state_json["spec"]["provider"]``. Without
    this, the new ``backend_for_stage(spec)`` plumbing is dead code.

    Also pins the wire-side echo: the resolved provider is observable on
    the 202 envelope AND on every ``TaskOut`` in ``GET /v1/jobs/{id}`` —
    otherwise a routing-resolved provider would be invisible to clients.
    """
    from app.adapters.registry import register_backend
    from app.adapters.stub_backend import StubBackend
    from app.db.session import get_session_factory

    register_backend("features_provider_backend", StubBackend, providers=["stub.features"])

    _, did = await _project_with_image(client)
    resp = await client.post(
        f"/v1/datasets/{did}/features",
        json={
            "spec": {
                "type": "sift",
                "provider": "stub.features",
                "use_gpu": False,
            }
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    task_id = body["task_ids"][0]

    # The 202 envelope echoes the provider.
    assert body["provider"] == "stub.features"

    # It is persisted into the worker's pre-execution state...
    persisted_spec = await _read_task_spec(get_session_factory(), task_id)
    assert persisted_spec.get("provider") == "stub.features"

    # ...and surfaced on every TaskOut in the job detail.
    detail = await client.get(f"/v1/jobs/{body['job_id']}")
    assert detail.status_code == 200, detail.text
    task_rows = detail.json()["tasks"]
    assert task_rows
    assert all(t["provider"] == "stub.features" for t in task_rows)


async def test_artifact_convert_persists_provider_into_task_spec(
    db_setup, client, monkeypatch
) -> None:
    """Same hand-off check on the artifact conversion path: the
    ``provider`` value from the request body lands in the persisted
    task spec, so the worker's ``backend_for_stage(spec)`` reads it.

    The existing artifact contract test asserts the 202 wire shape;
    this one asserts the DB persistence directly so a service-layer
    regression that drops ``provider`` between the request and the
    task row is caught.
    """
    from app.adapters.registry import register_backend
    from app.core.capabilities import reset_capabilities_cache
    from app.core.config import reset_settings_for_tests
    from app.core.hashing import content_address
    from app.core.ids import new_id
    from app.db.models import Job, Project, StageArtifact, Task
    from app.db.session import get_session_factory
    from tests.unit.test_artifact_contracts import (
        ArtifactConversionBackend,
        StubBackend,
    )

    monkeypatch.setenv("SFMAPI_BACKEND", "stub")
    register_backend("stub", StubBackend)
    register_backend("artifact_convert", ArtifactConversionBackend, providers=["artifact.convert"])
    reset_settings_for_tests()
    reset_capabilities_cache()

    factory = get_session_factory()
    async with factory() as session:
        project = Project(tenant_id="default", name="provider-plumbing")
        session.add(project)
        await session.flush()
        seed_job = Job(
            tenant_id="default",
            project_id=project.project_id,
            recipe="seed",
            spec_json={},
            status="succeeded",
        )
        session.add(seed_job)
        await session.flush()
        seed_task = Task(
            task_id=new_id(),
            tenant_id="default",
            job_id=seed_job.job_id,
            kind="seed",
            inputs_hash="i" * 64,
            params_hash="p" * 64,
            runtime_version_id="rv",
            cache_key=content_address(b"seed"),
            status="succeeded",
        )
        session.add(seed_task)
        await session.flush()
        source = StageArtifact(
            tenant_id="default",
            job_id=seed_job.job_id,
            task_id=seed_task.task_id,
            kind="matches.hloc_h5",
            name="hloc-matches",
            uri="memory://matches.h5",
            metadata_json={
                "artifact_format": "hloc.matches.h5.v1",
                "datatype": "match_graph",
                "schema_version": 1,
            },
        )
        session.add(source)
        await session.commit()
        source_id = source.artifact_id

    resp = await client.post(
        f"/v1/artifacts/{source_id}:convert",
        json={
            "provider": "artifact.convert",
            "accepted_formats": ["sfmapi.matches.indexed.v1"],
        },
    )
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_ids"][0]
    assert resp.json()["provider"] == "artifact.convert"

    persisted_spec = await _read_task_spec(factory, task_id)
    assert persisted_spec.get("provider") == "artifact.convert"


async def test_features_unknown_provider_returns_problem_with_candidates(
    client, monkeypatch
) -> None:
    """``ProviderAmbiguityError`` (and friends raised through
    ``apply_provider_resolution``) must reach the wire as an RFC 7807
    problem+json with the ``candidates`` and ``suggested_fix``
    extras populated. SDK ergonomics depend on this shape.

    We trigger the ambiguity branch directly by faking two enabled
    provider records with the same capability and no resolution rule.
    """
    from sfm_hub.models import ProviderManifest
    from sfm_hub.routing import ProviderRecord

    candidate_a = ProviderRecord(
        plugin_id="alpha_plugin",
        installed=True,
        enabled=True,
        runtime_modes=["uv"],
        provider=ProviderManifest(
            provider_id="alpha",
            display_name="alpha",
            capabilities=["features.extract.sift"],
        ),
    )
    candidate_b = ProviderRecord(
        plugin_id="beta_plugin",
        installed=True,
        enabled=True,
        runtime_modes=["uv"],
        provider=ProviderManifest(
            provider_id="beta",
            display_name="beta",
            capabilities=["features.extract.sift"],
        ),
    )

    import sfm_hub.routing as routing

    monkeypatch.setattr(
        routing,
        "provider_records",
        lambda **kwargs: [candidate_a, candidate_b],
    )
    monkeypatch.setattr(routing, "_candidate_records", lambda **kwargs: [candidate_a, candidate_b])

    _, did = await _project_with_image(client)
    resp = await client.post(
        f"/v1/datasets/{did}/features",
        json={"spec": {"type": "sift", "use_gpu": False}},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body.get("status") == 422
    assert body.get("candidates") == ["alpha", "beta"]
    assert "routing profile" in (body.get("suggested_fix") or "")
