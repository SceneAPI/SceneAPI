from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.e2e


def _symlink_or_marker(link: Path, target: Path, marker_names: set[str]) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        link.write_text(f"symlink marker for {target}: {exc}", encoding="utf-8")
        marker_names.add(link.name)
        return
    if not link.is_symlink():
        link.write_text(f"symlink marker for {target}", encoding="utf-8")
        marker_names.add(link.name)


async def _project_and_dataset(
    client,
    *,
    project_name: str = "radiance-p",
    dataset_name: str = "radiance-ds",
) -> tuple[str, str]:
    project = await client.post("/v1/projects", json={"name": project_name})
    assert project.status_code == 201, project.text
    project_id = project.json()["project_id"]
    dataset = await client.post(
        f"/v1/projects/{project_id}/datasets",
        json={
            "name": dataset_name,
            "source": {"kind": "upload"},
            "camera_model": "SIMPLE_RADIAL",
            "intrinsics_mode": "single_camera",
        },
    )
    assert dataset.status_code == 201, dataset.text
    return project_id, dataset.json()["dataset_id"]


def _start_gsplat_pseudo_service(
    snapshot_root: Path,
    captured: dict[str, Any],
) -> tuple[ThreadingHTTPServer, Thread, str]:
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._json(200, {"status": "ok"})
                return
            if self.path == "/version":
                self._json(
                    200,
                    {
                        "protocol": "sfmapi-plugin-http-v1",
                        "protocol_version": "1.0",
                        "provider": "gsplat",
                    },
                )
                return
            if self.path == "/datatypes":
                self._json(
                    200,
                    {
                        "schema_version": 1,
                        "plugin_id": "gsplat",
                        "datatypes": [],
                    },
                )
                return
            if self.path == "/processors":
                self._json(
                    200,
                    {
                        "schema_version": 1,
                        "plugin_id": "gsplat",
                        "processors": [],
                        "processor_extensions": [],
                    },
                )
                return
            if self.path == "/pipelines":
                self._json(
                    200,
                    {
                        "schema_version": 1,
                        "plugin_id": "gsplat",
                        "pipelines": [],
                    },
                )
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/execute":
                self._json(404, {"error": "not_found"})
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = json.loads(body.decode("utf-8"))
            captured["execute"] = payload

            spec = payload["spec"]
            inputs = payload["inputs"]
            max_steps = int(spec["max_steps"])
            radiance_field_id = inputs["radiance_field_id"]
            from app.core.paths import Paths

            out_dir = (
                Paths().radiance_field_root(
                    payload["tenant_id"],
                    inputs["project_id"],
                    radiance_field_id,
                )
                / "_live"
                / snapshot_root.name
            )
            captured["snapshot_path"] = str(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

            checkpoints = {1, max_steps // 3, (max_steps * 2) // 3, max_steps}
            metrics: list[dict[str, float | int]] = []
            loss = 1.0
            for step in range(1, max_steps + 1):
                loss = (loss * 0.9975) + (1.0 / (step + 64) * 0.0025)
                if step in checkpoints:
                    progress = step / max_steps
                    metrics.append(
                        {
                            "step": step,
                            "loss": round(loss, 6),
                            "psnr": round(18.0 + progress * 12.0, 4),
                        }
                    )

            summary = {
                "provider": payload["provider"],
                "method": spec["method"],
                "dataset_id": inputs.get("dataset_id"),
                "dataset_label": spec.get("backend_options", {}).get("dataset_label"),
                "training_preset": spec.get("backend_options", {}).get("training_preset"),
                "pseudo_training": True,
                "max_steps": max_steps,
                "completed_steps": max_steps,
                "loss_initial": metrics[0]["loss"],
                "loss_final": metrics[-1]["loss"],
                "psnr_final": metrics[-1]["psnr"],
                "vertex_count": 3,
                "format": "ply",
            }
            (out_dir / "summary.json").write_text(
                json.dumps(summary, sort_keys=True),
                encoding="utf-8",
            )
            (out_dir / "metadata.json").write_text(
                json.dumps({"protocol": payload["protocol"], **summary}, sort_keys=True),
                encoding="utf-8",
            )
            (out_dir / "metrics.json").write_text(
                json.dumps({"max_steps": max_steps, "samples": metrics}, sort_keys=True),
                encoding="utf-8",
            )
            ply = out_dir / "point_cloud.ply"
            ply.write_text(
                "\n".join(
                    [
                        "ply",
                        "format ascii 1.0",
                        "element vertex 3",
                        "property float x",
                        "property float y",
                        "property float z",
                        "end_header",
                        "0 0 0",
                        "1 0 0",
                        "0 1 0",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            self._json(
                200,
                {
                    "status": "succeeded",
                    "outputs": {
                        "radiance_field_id": radiance_field_id,
                        "snapshot_seq": 1,
                        "snapshot_path": str(out_dir),
                        "summary": summary,
                        "artifacts": [
                            {
                                "kind": "radiance.snapshot",
                                "name": "snapshot-1",
                                "uri": str(out_dir),
                                "artifact_format": "sfmapi.radiance.snapshot.v1",
                                "metadata": {
                                    "radiance_field_id": radiance_field_id,
                                    "snapshot_seq": 1,
                                },
                                "summary": summary,
                            },
                            {
                                "kind": "radiance.variant.ply",
                                "name": "point_cloud.ply",
                                "uri": str(ply),
                                "media_type": "application/octet-stream",
                                "artifact_format": "sfmapi.radiance.variant.ply.v1",
                                "metadata": {
                                    "radiance_field_id": radiance_field_id,
                                    "snapshot_seq": 1,
                                },
                                "summary": {"vertex_count": 3},
                            },
                        ],
                        "variants": [
                            {
                                "format": "ply",
                                "uri": str(ply),
                                "media_type": "application/octet-stream",
                                "summary": {"vertex_count": 3},
                            }
                        ],
                    },
                },
            )

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"


async def test_radiance_stub_train_creates_field_job_and_snapshot(client) -> None:
    project_id, dataset_id = await _project_and_dataset(client)

    response = await client.post(
        f"/v1/projects/{project_id}/radiance_fields:train",
        json={
            "name": "stub-field",
            "dataset_id": dataset_id,
            "provider": "stub",
            "method": "stub",
            "max_steps": 1,
        },
    )

    assert response.status_code == 202, response.text
    assert response.headers["location"].startswith("/v1/jobs/")
    accepted = response.json()
    radiance_field_id = accepted["radiance_field_id"]
    assert accepted["dataset_id"] == dataset_id
    assert accepted["provider"] == "stub"

    job = await client.get(response.headers["location"])
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "succeeded"
    assert job.json()["tasks"][0]["kind"] == "radiance_train"

    field = await client.get(f"/v1/radiance_fields/{radiance_field_id}")
    assert field.status_code == 200, field.text
    field_body = field.json()
    assert field_body["status"] == "succeeded"
    assert field_body["summary"]["vertex_count"] == 1
    assert field_body["_links"]["snapshots"]["href"].endswith("/snapshots")

    listed = await client.get(f"/v1/projects/{project_id}/radiance_fields")
    assert listed.status_code == 200, listed.text
    assert [item["radiance_field_id"] for item in listed.json()["items"]] == [radiance_field_id]

    snapshots = await client.get(f"/v1/radiance_fields/{radiance_field_id}/snapshots")
    assert snapshots.status_code == 200, snapshots.text
    assert snapshots.json()["seqs"] == [1]

    ply = await client.get(f"/v1/radiance_fields/{radiance_field_id}/snapshots/1/point_cloud.ply")
    assert ply.status_code == 200, ply.text
    assert "element vertex 1" in ply.text


async def test_radiance_stub_train_eval_records_metrics_and_time(client) -> None:
    project_id, dataset_id = await _project_and_dataset(client)

    response = await client.post(
        f"/v1/projects/{project_id}/radiance_fields:train",
        json={
            "name": "stub-field-eval",
            "dataset_id": dataset_id,
            "provider": "stub",
            "method": "stub",
            "max_steps": 1,
            "eval": {
                "enabled": True,
                "split": "test",
                "final": True,
                "metrics": ["psnr", "ssim", "lpips"],
            },
        },
    )

    assert response.status_code == 202, response.text
    accepted = response.json()
    evaluation_id = accepted["radiance_evaluation_id"]
    assert isinstance(evaluation_id, str)

    evaluation = await client.get(f"/v1/radiance_evaluations/{evaluation_id}")
    assert evaluation.status_code == 200, evaluation.text
    body = evaluation.json()
    assert body["status"] == "succeeded"
    assert body["metrics"]["psnr_db"] == 30.0
    assert body["metrics"]["ssim"] == 1.0
    assert body["metrics"]["lpips"] == 0.0
    assert body["metrics"]["duration_s"] == 0.0
    assert body["metrics"]["render_time_s_total"] == 0.0

    metrics = await client.get(f"/v1/radiance_evaluations/{evaluation_id}/metrics")
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()["num_images"] == 1

    listed = await client.get(f"/v1/radiance_fields/{accepted['radiance_field_id']}/evaluations")
    assert listed.status_code == 200, listed.text
    assert [item["evaluation_id"] for item in listed.json()["items"]] == [evaluation_id]


async def test_radiance_stub_standalone_eval_records_metrics_and_time(client) -> None:
    project_id, dataset_id = await _project_and_dataset(client)
    train = await client.post(
        f"/v1/projects/{project_id}/radiance_fields:train",
        json={
            "name": "stub-field-standalone-eval",
            "dataset_id": dataset_id,
            "provider": "stub",
            "method": "stub",
            "max_steps": 1,
        },
    )
    assert train.status_code == 202, train.text
    radiance_field_id = train.json()["radiance_field_id"]

    response = await client.post(
        f"/v1/radiance_fields/{radiance_field_id}:evaluate",
        json={
            "snapshot_seq": 1,
            "eval": {
                "enabled": True,
                "split": "test",
                "metrics": ["psnr", "ssim", "lpips"],
            },
        },
    )

    assert response.status_code == 202, response.text
    evaluation_id = response.json()["radiance_evaluation_id"]
    metrics = await client.get(f"/v1/radiance_evaluations/{evaluation_id}/metrics")
    assert metrics.status_code == 200, metrics.text
    body = metrics.json()
    assert body["psnr_db"] == 30.0
    assert body["ssim"] == 1.0
    assert body["lpips"] == 0.0
    assert body["duration_s"] == 0.0


async def test_radiance_evaluate_rejects_dataset_from_other_project(client) -> None:
    project_id, dataset_id = await _project_and_dataset(client)
    _other_project_id, other_dataset_id = await _project_and_dataset(
        client,
        project_name="radiance-p-other",
        dataset_name="radiance-ds-other",
    )
    train = await client.post(
        f"/v1/projects/{project_id}/radiance_fields:train",
        json={
            "name": "stub-field-cross-project-eval",
            "dataset_id": dataset_id,
            "provider": "stub",
            "method": "stub",
            "max_steps": 1,
        },
    )
    assert train.status_code == 202, train.text

    response = await client.post(
        f"/v1/radiance_fields/{train.json()['radiance_field_id']}:evaluate",
        json={
            "dataset_id": other_dataset_id,
            "snapshot_seq": 1,
            "eval": {"enabled": True, "split": "test", "metrics": ["psnr"]},
        },
    )

    assert response.status_code == 404, response.text


async def test_radiance_train_rejects_ambiguous_inputs(client) -> None:
    project_id, dataset_id = await _project_and_dataset(client)

    response = await client.post(
        f"/v1/projects/{project_id}/radiance_fields:train",
        json={"dataset_id": dataset_id, "recon_id": "01H00000000000000000000000"},
    )

    assert response.status_code == 422


async def test_radiance_train_rejects_non_container_radiance_provider(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import radiance_service

    monkeypatch.setattr(
        radiance_service,
        "provider_records",
        lambda installed_only=False, enabled_only=False: [
            SimpleNamespace(
                plugin_id="uv_radiance",
                runtime_modes=["uv"],
                provider=SimpleNamespace(
                    provider_id="uv_radiance",
                    capabilities=["radiance.train"],
                ),
            )
        ],
    )
    project_id, dataset_id = await _project_and_dataset(client)

    response = await client.post(
        f"/v1/projects/{project_id}/radiance_fields:train",
        json={
            "dataset_id": dataset_id,
            "provider": "uv_radiance",
            "method": "uv.train",
            "max_steps": 1,
        },
    )

    assert response.status_code == 501, response.text
    assert "container_service runtime" in response.json()["detail"]


async def test_radiance_train_resolves_omitted_provider(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import radiance_service

    captured: dict[str, Any] = {}

    def fake_apply_provider_resolution(spec: dict[str, Any], **kwargs: object) -> None:
        captured["resolution"] = kwargs
        assert "provider" not in spec
        spec["provider"] = "gsplat"

    async def fake_submit_job_dag(*_args: object, **kwargs: object):
        captured["job_spec"] = kwargs["spec"]
        node = kwargs["nodes"][0]
        return "job-routed", [SimpleNamespace(task_id=node.task_id)]

    monkeypatch.setattr(
        radiance_service,
        "apply_provider_resolution",
        fake_apply_provider_resolution,
    )
    monkeypatch.setattr(
        radiance_service,
        "_require_radiance_provider_capabilities",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(radiance_service, "submit_job_dag", fake_submit_job_dag)
    project_id, dataset_id = await _project_and_dataset(client)

    response = await client.post(
        f"/v1/projects/{project_id}/radiance_fields:train",
        json={"dataset_id": dataset_id, "method": "gsplat.train.default"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["provider"] == "gsplat"
    assert captured["resolution"]["stage"] == "radiance"
    assert captured["resolution"]["capability"] == "radiance.train"
    assert captured["job_spec"]["provider"] == "gsplat"


async def test_radiance_train_result_rejects_snapshot_path_outside_workspace(
    session,
    tmp_path: Path,
) -> None:
    from app.core.errors import ValidationError
    from app.core.ids import new_id
    from app.db.models import Project, RadianceField
    from app.services.radiance_service import record_radiance_train_result

    outside = tmp_path / "outside-provider"
    outside.mkdir()
    (outside / "summary.json").write_text("{}", encoding="utf-8")
    project_id = new_id()
    radiance_field_id = new_id()
    session.add(
        Project(
            project_id=project_id,
            tenant_id="default",
            name="radiance-outside-provider",
        )
    )
    session.add(
        RadianceField(
            radiance_field_id=radiance_field_id,
            tenant_id="default",
            project_id=project_id,
            dataset_id=None,
            recon_id=None,
            name="outside-provider",
            provider="gsplat",
            method="gsplat.train.default",
            status="running",
            spec_json={},
        )
    )
    await session.flush()

    with pytest.raises(ValidationError, match="snapshot_path must stay"):
        await record_radiance_train_result(
            session,
            tenant_id="default",
            radiance_field_id=radiance_field_id,
            outputs={
                "snapshot_seq": 1,
                "snapshot_path": str(outside),
                "summary": {"provider": "gsplat"},
            },
        )


async def test_radiance_train_result_rejects_snapshot_path_from_other_field(
    session,
) -> None:
    from app.core.errors import ValidationError
    from app.core.ids import new_id
    from app.core.paths import Paths
    from app.db.models import Project, RadianceField
    from app.services.radiance_service import record_radiance_train_result

    project_id = new_id()
    radiance_field_id = new_id()
    session.add(
        Project(
            project_id=project_id,
            tenant_id="default",
            name="radiance-cross-field-provider",
        )
    )
    session.add(
        RadianceField(
            radiance_field_id=radiance_field_id,
            tenant_id="default",
            project_id=project_id,
            dataset_id=None,
            recon_id=None,
            name="cross-field-provider",
            provider="gsplat",
            method="gsplat.train.default",
            status="running",
            spec_json={},
        )
    )
    await session.flush()
    other = (
        Paths().radiance_field_root("default", project_id, new_id())
        / "_live"
        / "provider"
    )
    other.mkdir(parents=True, exist_ok=True)
    (other / "summary.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValidationError, match="current radiance field root"):
        await record_radiance_train_result(
            session,
            tenant_id="default",
            radiance_field_id=radiance_field_id,
            outputs={
                "snapshot_seq": 1,
                "snapshot_path": str(other),
                "summary": {"provider": "gsplat"},
            },
        )


async def test_radiance_results_store_only_public_provider_outputs(session) -> None:
    from sqlalchemy import select

    from app.core.ids import new_id
    from app.db.models import (
        Project,
        RadianceEvaluation,
        RadianceField,
        RadianceSnapshot,
    )
    from app.services.radiance_service import record_radiance_train_result

    project_id = new_id()
    radiance_field_id = new_id()
    evaluation_id = new_id()
    session.add(
        Project(
            project_id=project_id,
            tenant_id="default",
            name="radiance-public-outputs",
        )
    )
    session.add(
        RadianceField(
            radiance_field_id=radiance_field_id,
            tenant_id="default",
            project_id=project_id,
            dataset_id=None,
            recon_id=None,
            name="public-outputs",
            provider="gsplat",
            method="gsplat.train.default",
            status="running",
            spec_json={},
        )
    )
    session.add(
        RadianceEvaluation(
            evaluation_id=evaluation_id,
            tenant_id="default",
            radiance_field_id=radiance_field_id,
            snapshot_seq=1,
            provider="gsplat",
            method="gsplat.eval.default",
            split="test",
            status="running",
            config_json={},
        )
    )
    await session.flush()

    await record_radiance_train_result(
        session,
        tenant_id="default",
        radiance_field_id=radiance_field_id,
        expected_evaluation_id=evaluation_id,
        outputs={
            "snapshot_seq": 1,
            "snapshot_path": "mem://snapshots/rf/1",
            "summary": {
                "provider": "gsplat",
                "service_url": "http://127.0.0.1/private",
                "token": "SECRET_BODY",
            },
            "metadata": {
                "debug_path": "C:/secret/snapshot.json",
                "service_url": "http://127.0.0.1/private",
                "env": "SFMAPI_GSPLAT_SERVICE_URL",
                "note": "kept",
            },
            "evaluations": [
                {
                    "evaluation_id": evaluation_id,
                    "radiance_field_id": radiance_field_id,
                    "metrics": {
                        "psnr": 30.0,
                        "service_url": "http://127.0.0.1/metrics",
                        "token": "SECRET_BODY",
                    },
                    "artifacts": [
                        {
                            "name": "metrics",
                            "uri": "mem://metrics.json",
                            "metadata": {
                                "authorization": "Bearer SECRET_BODY",
                                "note": "kept",
                            },
                        }
                    ],
                }
            ],
        },
    )

    snapshot = (
        await session.execute(
            select(RadianceSnapshot).where(
                RadianceSnapshot.radiance_field_id == radiance_field_id
            )
        )
    ).scalar_one()
    evaluation = (
        await session.execute(
            select(RadianceEvaluation).where(
                RadianceEvaluation.evaluation_id == evaluation_id
            )
        )
    ).scalar_one()

    assert snapshot.summary_json == {
        "provider": "gsplat",
        "service_url": "<redacted>",
    }
    assert evaluation.metrics_json == {"psnr": 30.0, "service_url": "<redacted>"}
    assert evaluation.artifacts_json == [
        {
            "name": "metrics",
            "uri": "mem://metrics.json",
            "metadata": {"note": "kept"},
        }
    ]
    persisted_metadata = json.loads(
        (Path(snapshot.sealed_path) / "metadata.json").read_text(encoding="utf-8")
    )
    assert persisted_metadata == {
        "debug_path": "<redacted>",
        "service_url": "<redacted>",
        "note": "kept",
    }
    public_repr = repr(
        [snapshot.summary_json, evaluation.metrics_json, evaluation.artifacts_json, persisted_metadata]
    )
    assert "SECRET" not in public_repr
    assert "SFMAPI_" not in public_repr
    assert "127.0.0.1" not in public_repr
    assert "C:/secret" not in public_repr


async def test_radiance_train_result_rejects_live_snapshot_symlink_escapes(
    session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core.errors import ValidationError
    from app.core.ids import new_id
    from app.core.paths import Paths
    from app.db.models import Project, RadianceField
    from app.services import radiance_service

    project_id = new_id()
    radiance_field_id = new_id()
    session.add(
        Project(
            project_id=project_id,
            tenant_id="default",
            name="radiance-symlink-provider",
        )
    )
    session.add(
        RadianceField(
            radiance_field_id=radiance_field_id,
            tenant_id="default",
            project_id=project_id,
            dataset_id=None,
            recon_id=None,
            name="symlink-provider",
            provider="gsplat",
            method="gsplat.train.default",
            status="running",
            spec_json={},
        )
    )
    await session.flush()

    live_root = (
        Paths().radiance_field_root("default", project_id, radiance_field_id)
        / "_live"
        / "provider"
    )
    live_root.mkdir(parents=True, exist_ok=True)
    outside_payload = tmp_path / "outside-point-cloud.ply"
    outside_payload.write_text("outside ply", encoding="utf-8")
    outside_summary = tmp_path / "outside-summary.json"
    marker_names: set[str] = set()
    _symlink_or_marker(live_root / "point_cloud.ply", outside_payload, marker_names)
    _symlink_or_marker(live_root / "summary.json", outside_summary, marker_names)
    if marker_names:
        original_is_link = radiance_service._is_symlink_or_junction
        monkeypatch.setattr(
            radiance_service,
            "_is_symlink_or_junction",
            lambda path: path.name in marker_names or original_is_link(path),
        )

    with pytest.raises(ValidationError, match="symlinks"):
        await radiance_service.record_radiance_train_result(
            session,
            tenant_id="default",
            radiance_field_id=radiance_field_id,
            outputs={
                "snapshot_seq": 1,
                "snapshot_path": str(live_root),
                "summary": {"provider": "gsplat"},
            },
        )
    assert not outside_summary.exists()


async def test_radiance_train_result_rejects_missing_absolute_live_snapshot_path(
    session,
) -> None:
    from app.core.errors import ValidationError
    from app.core.ids import new_id
    from app.core.paths import Paths
    from app.db.models import Project, RadianceField
    from app.services.radiance_service import record_radiance_train_result

    project_id = new_id()
    radiance_field_id = new_id()
    session.add(
        Project(
            project_id=project_id,
            tenant_id="default",
            name="radiance-missing-provider",
        )
    )
    session.add(
        RadianceField(
            radiance_field_id=radiance_field_id,
            tenant_id="default",
            project_id=project_id,
            dataset_id=None,
            recon_id=None,
            name="missing-provider",
            provider="gsplat",
            method="gsplat.train.default",
            status="running",
            spec_json={},
        )
    )
    await session.flush()

    missing_live_root = (
        Paths().radiance_field_root("default", project_id, radiance_field_id)
        / "_live"
        / "deleted-provider-run"
    )

    with pytest.raises(ValidationError, match="existing directory"):
        await record_radiance_train_result(
            session,
            tenant_id="default",
            radiance_field_id=radiance_field_id,
            outputs={
                "snapshot_seq": 1,
                "snapshot_path": str(missing_live_root),
                "summary": {"provider": "gsplat"},
            },
        )


@pytest.mark.parametrize("snapshot_path", ["snapshots/run-1", "urn:sfmapi:radiance:1", "C:run-1"])
async def test_radiance_train_result_rejects_non_explicit_local_snapshot_path(
    session,
    snapshot_path: str,
) -> None:
    from app.core.errors import ValidationError
    from app.core.ids import new_id
    from app.db.models import Project, RadianceField
    from app.services.radiance_service import record_radiance_train_result

    project_id = new_id()
    radiance_field_id = new_id()
    session.add(
        Project(
            project_id=project_id,
            tenant_id="default",
            name="radiance-relative-provider",
        )
    )
    session.add(
        RadianceField(
            radiance_field_id=radiance_field_id,
            tenant_id="default",
            project_id=project_id,
            dataset_id=None,
            recon_id=None,
            name="relative-provider",
            provider="gsplat",
            method="gsplat.train.default",
            status="running",
            spec_json={},
        )
    )
    await session.flush()

    with pytest.raises(ValidationError, match="absolute local path"):
        await record_radiance_train_result(
            session,
            tenant_id="default",
            radiance_field_id=radiance_field_id,
            outputs={
                "snapshot_seq": 1,
                "snapshot_path": snapshot_path,
                "summary": {"provider": "gsplat"},
            },
        )


@pytest.mark.parametrize("snapshot_seq", [0, -1, True])
async def test_radiance_train_result_rejects_invalid_snapshot_seq(
    session,
    snapshot_seq: object,
) -> None:
    from app.core.errors import ValidationError
    from app.core.ids import new_id
    from app.db.models import Project, RadianceField
    from app.services.radiance_service import record_radiance_train_result

    project_id = new_id()
    radiance_field_id = new_id()
    session.add(
        Project(
            project_id=project_id,
            tenant_id="default",
            name=f"radiance-bad-seq-{snapshot_seq!r}",
        )
    )
    session.add(
        RadianceField(
            radiance_field_id=radiance_field_id,
            tenant_id="default",
            project_id=project_id,
            dataset_id=None,
            recon_id=None,
            name="bad-seq-provider",
            provider="gsplat",
            method="gsplat.train.default",
            status="running",
            spec_json={},
        )
    )
    await session.flush()

    with pytest.raises(ValidationError, match="snapshot_seq"):
        await record_radiance_train_result(
            session,
            tenant_id="default",
            radiance_field_id=radiance_field_id,
            outputs={
                "snapshot_seq": snapshot_seq,
                "snapshot_path": "mem://snapshots/rf/1",
                "summary": {"provider": "gsplat"},
            },
        )


async def test_gsplat_container_service_pseudo_train_3000_steps(
    client,
    session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from sqlalchemy import select

    from app.core.paths import Paths
    from app.db.models import RadianceSnapshot

    snapshot_root = Paths().workspace_root / "_test_pseudo_gsplat_snapshots" / tmp_path.name
    captured: dict[str, Any] = {}
    server, thread, base_url = _start_gsplat_pseudo_service(
        snapshot_root,
        captured,
    )
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        install = await client.post(
            "/v1/admin/plugins/gsplat:install",
            json={
                "method": "container_service",
                "dry_run": False,
                "allow_unsafe_execution": True,
                "request_id": "550e8400-e29b-41d4-a716-446655443000",
                "provision_runtime": False,
            },
        )
        assert install.status_code == 200, install.text
        assert install.json()["installed"] is True

        doctor = await client.post("/v1/admin/plugins/gsplat:doctor")
        assert doctor.status_code == 200, doctor.text
        container_check = next(
            item for item in doctor.json()["checks"] if item["name"] == "container_service"
        )
        assert container_check["status"] == "pass"

        project_id, dataset_id = await _project_and_dataset(client)
        accepted = await client.post(
            f"/v1/projects/{project_id}/radiance_fields:train",
            json={
                "name": "gsplat-pseudo-3000",
                "dataset_id": dataset_id,
                "provider": "gsplat@gsplat",
                "method": "gsplat.train.default",
                "max_steps": 3000,
                "backend_options": {
                    "dataset_label": "bicycle-contract",
                    "training_preset": "pseudo-3000",
                },
            },
        )
        assert accepted.status_code == 202, accepted.text
        accepted_body = accepted.json()
        radiance_field_id = accepted_body["radiance_field_id"]

        assert captured["execute"]["protocol"] == "sfmapi-plugin-http-v1"
        assert captured["execute"]["task_kind"] == "radiance_train"
        assert captured["execute"]["provider"] == "gsplat@gsplat"
        assert captured["execute"]["spec"]["max_steps"] == 3000

        job = await client.get(accepted.headers["location"])
        assert job.status_code == 200, job.text
        job_body = job.json()
        assert job_body["status"] == "succeeded"
        assert job_body["tasks"][0]["status"] == "succeeded"
        assert job_body["tasks"][0]["provider"] == "gsplat@gsplat"

        field = await client.get(f"/v1/radiance_fields/{radiance_field_id}")
        assert field.status_code == 200, field.text
        summary = field.json()["summary"]
        assert field.json()["status"] == "succeeded"
        assert summary["pseudo_training"] is True
        assert summary["completed_steps"] == 3000
        assert summary["dataset_label"] == "bicycle-contract"
        assert summary["loss_final"] < summary["loss_initial"]

        snapshots = await client.get(f"/v1/radiance_fields/{radiance_field_id}/snapshots")
        assert snapshots.status_code == 200, snapshots.text
        assert snapshots.json()["seqs"] == [1]
        snapshot_row = (
            (
                await session.execute(
                    select(RadianceSnapshot).where(
                        RadianceSnapshot.radiance_field_id == radiance_field_id,
                        RadianceSnapshot.seq == 1,
                    )
                )
            )
            .scalars()
            .one()
        )
        managed_root = Paths().radiance_field_root("default", project_id, radiance_field_id)
        sealed_path = Path(snapshot_row.sealed_path)
        assert sealed_path.resolve() != Path(captured["snapshot_path"]).resolve()
        assert sealed_path.resolve().is_relative_to(managed_root.resolve())

        metrics = await client.get(
            f"/v1/radiance_fields/{radiance_field_id}/snapshots/1/metrics.json"
        )
        assert metrics.status_code == 200, metrics.text
        metrics_body = metrics.json()
        assert metrics_body["max_steps"] == 3000
        assert metrics_body["samples"][-1]["step"] == 3000

        ply = await client.get(
            f"/v1/radiance_fields/{radiance_field_id}/snapshots/1/point_cloud.ply"
        )
        assert ply.status_code == 200, ply.text
        assert "element vertex 3" in ply.text

        artifacts = await client.get(f"/v1/jobs/{accepted_body['job_id']}/artifacts")
        assert artifacts.status_code == 200, artifacts.text
        assert [item["kind"] for item in artifacts.json()["items"]] == [
            "radiance.snapshot",
            "radiance.variant.ply",
        ]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
