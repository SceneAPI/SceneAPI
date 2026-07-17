from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from sceneapi.server.core.errors import CapabilityUnavailableError, ValidationError
from sceneapi.server.schemas.api.radiance import RadianceEvaluateRequest, RadianceTrainRequest
from sceneapi.server.workers.tasks.radiance_eval import run as run_eval
from sceneapi.server.workers.tasks.radiance_train import run
from sfm_hub.state import record_manual_install

pytestmark = pytest.mark.unit


def test_radiance_requests_accept_max_length_plugin_qualified_provider() -> None:
    provider = ("p" * 64) + "@" + ("g" * 64)

    train = RadianceTrainRequest.model_validate(
        {
            "dataset_id": "dataset-1",
            "provider": provider,
        }
    )
    evaluate = RadianceEvaluateRequest.model_validate({"provider": provider})

    assert train.provider == provider
    assert evaluate.provider == provider


def test_radiance_requests_reject_overlength_provider_selector_components() -> None:
    with pytest.raises(PydanticValidationError):
        RadianceTrainRequest.model_validate(
            {
                "dataset_id": "dataset-1",
                "provider": "p" * 65,
            }
        )

    with pytest.raises(PydanticValidationError):
        RadianceEvaluateRequest.model_validate(
            {
                "provider": ("p" * 64) + "@" + ("g" * 65),
            }
        )


def _start_execute_service(
    captured: dict[str, Any],
) -> tuple[ThreadingHTTPServer, Thread, str]:
    class ExecuteHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            captured["path"] = self.path
            captured["body"] = json.loads(body.decode("utf-8"))
            response = captured.get("response")
            if response is None:
                response = {
                    "status": "succeeded",
                    "outputs": {
                        "radiance_field_id": captured["body"]["inputs"]["radiance_field_id"],
                        "snapshot_seq": 7,
                        "snapshot_path": "mem://snapshots/rf-1/7",
                        "summary": {
                            "provider": captured["body"]["provider"],
                            "method": captured["body"]["spec"]["method"],
                        },
                        "artifacts": [],
                        "variants": [],
                    },
                }
            payload = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExecuteHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"


def test_radiance_train_dispatches_installed_container_service_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-1",
                    "dataset_id": "dataset-1",
                    "recon_id": None,
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.train.default",
                    "max_steps": 5,
                    "backend_options": {"init_type": "sfm"},
                },
            },
        )

        outputs = run(task)  # type: ignore[arg-type]

        assert captured["path"] == "/execute"
        assert captured["body"]["protocol"] == "sfmapi-plugin-http-v1"
        # Kit-based container plugins speak protocol 1.1 (register L40);
        # the bundled registry mirrors their manifests since the W9 sweep.
        assert captured["body"]["protocol_version"] == "1.1"
        assert captured["body"]["task_kind"] == "radiance_train"
        assert captured["body"]["provider"] == "gsplat"
        assert captured["body"]["spec"]["backend_options"] == {"init_type": "sfm"}
        assert outputs["snapshot_seq"] == 7
        assert outputs["summary"] == {
            "provider": "gsplat",
            "method": "gsplat.train.default",
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_train_accepts_plugin_qualified_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-1",
                    "dataset_id": "dataset-1",
                },
                "spec": {
                    "provider": "gsplat@gsplat",
                    "method": "gsplat.train.default",
                    "max_steps": 5,
                },
            },
        )

        outputs = run(task)  # type: ignore[arg-type]

        assert captured["path"] == "/execute"
        assert captured["body"]["provider"] == "gsplat@gsplat"
        assert outputs["snapshot_seq"] == 7
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_train_rejects_nonterminal_provider_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "running",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "snapshot_seq": 7,
                "snapshot_path": "mem://snapshots/rf-provider/7",
                "artifacts": [],
                "variants": [],
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-1",
                    "dataset_id": "dataset-1",
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.train.default",
                    "max_steps": 5,
                },
            },
        )

        with pytest.raises(RuntimeError, match="radiance provider 'gsplat' failed"):
            run(task)  # type: ignore[arg-type]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_train_accepts_single_anonymous_nested_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "succeeded",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "snapshot_seq": 7,
                "snapshot_path": "mem://snapshots/rf-provider/7",
                "evaluations": [
                    {
                        "radiance_field_id": "rf-provider",
                        "metrics": {"psnr": 31.0},
                    }
                ],
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-task",
                    "evaluation_id": "ev-task",
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.train.default",
                    "eval": {"enabled": True},
                },
            },
        )

        outputs = run(task)  # type: ignore[arg-type]

        assert outputs["radiance_field_id"] == "rf-task"
        assert outputs["evaluation_id"] == "ev-task"
        assert outputs["evaluations"][0]["radiance_field_id"] == "rf-task"
        assert outputs["evaluations"][0]["evaluation_id"] == "ev-task"
        assert outputs["evaluations"][0]["metrics"] == {"psnr": 31.0}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_train_rejects_metric_evaluation_without_requested_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "succeeded",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "snapshot_seq": 7,
                "snapshot_path": "mem://snapshots/rf-provider/7",
                "evaluations": [
                    {
                        "radiance_field_id": "rf-provider",
                        "evaluation_id": "ev-foreign",
                        "metrics": {"psnr": 31.0},
                    }
                ],
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-task",
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.train.default",
                },
            },
        )

        with pytest.raises(ValidationError):
            run(task)  # type: ignore[arg-type]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_train_rejects_top_level_evaluation_without_requested_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "succeeded",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "snapshot_seq": 7,
                "snapshot_path": "mem://snapshots/rf-provider/7",
                "evaluation_id": "ev-existing",
                "metrics": {"psnr": 31.0},
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-task",
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.train.default",
                },
            },
        )

        with pytest.raises(ValidationError):
            run(task)  # type: ignore[arg-type]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_train_rejects_conflicting_nested_evaluation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "succeeded",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "snapshot_seq": 7,
                "snapshot_path": "mem://snapshots/rf-provider/7",
                "evaluations": [
                    {
                        "radiance_field_id": "rf-provider",
                        "evaluation_id": "ev-provider",
                        "metrics": {"psnr": 31.0},
                    }
                ],
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-task",
                    "evaluation_id": "ev-task",
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.train.default",
                    "eval": {"enabled": True},
                },
            },
        )

        with pytest.raises(ValidationError):
            run(task)  # type: ignore[arg-type]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_train_rejects_extra_metric_evaluation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "succeeded",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "snapshot_seq": 7,
                "snapshot_path": "mem://snapshots/rf-provider/7",
                "evaluations": [
                    {
                        "radiance_field_id": "rf-provider",
                        "evaluation_id": "ev-task",
                        "metrics": {"psnr": 31.0},
                    },
                    {
                        "radiance_field_id": "rf-provider",
                        "evaluation_id": "ev-other",
                        "metrics": {"psnr": 29.0},
                    },
                ],
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-task",
                    "evaluation_id": "ev-task",
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.train.default",
                    "eval": {"enabled": True},
                },
            },
        )

        with pytest.raises(ValidationError):
            run(task)  # type: ignore[arg-type]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_train_rejects_duplicate_metric_evaluation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "succeeded",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "snapshot_seq": 7,
                "snapshot_path": "mem://snapshots/rf-provider/7",
                "evaluations": [
                    {
                        "radiance_field_id": "rf-provider",
                        "evaluation_id": "ev-task",
                        "metrics": {"psnr": 31.0},
                    },
                    {
                        "radiance_field_id": "rf-provider",
                        "evaluation_id": "ev-task",
                        "metrics": {"psnr": 29.0},
                    },
                ],
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-task",
                    "evaluation_id": "ev-task",
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.train.default",
                    "eval": {"enabled": True},
                },
            },
        )

        with pytest.raises(ValidationError):
            run(task)  # type: ignore[arg-type]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    "artifacts",
    [
        [{"kind": "radiance.metrics", "uri": "mem://metrics.json"}],
        [{"kind": "radiance.metrics", "files": [{}]}],
    ],
)
def test_radiance_train_rejects_duplicate_same_id_artifact_row(
    monkeypatch: pytest.MonkeyPatch,
    artifacts: Any,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "succeeded",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "snapshot_seq": 7,
                "snapshot_path": "mem://snapshots/rf-provider/7",
                "evaluations": [
                    {
                        "radiance_field_id": "rf-provider",
                        "evaluation_id": "ev-task",
                        "metrics": {"psnr": 31.0},
                    },
                    {
                        "radiance_field_id": "rf-provider",
                        "evaluation_id": "ev-task",
                        "artifacts": artifacts,
                    },
                ],
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-task",
                    "evaluation_id": "ev-task",
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.train.default",
                    "eval": {"enabled": True},
                },
            },
        )

        with pytest.raises(ValidationError):
            run(task)  # type: ignore[arg-type]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    "artifacts",
    [
        {},
        [{"kind": "radiance.metrics", "artifact_format": "bad format"}],
        [{"kind": "radiance.metrics", "datatype": "bad type"}],
        [{"kind": "radiance.metrics", "schema_version": 0}],
        [{"kind": "radiance.metrics", "summary": []}],
        [{"kind": "radiance.metrics", "metadata": []}],
        [{"kind": "radiance.metrics", "metadata": {"sha256": "A" * 64}}],
        [{"kind": "radiance.metrics", "metadata": {"byte_size": -1}}],
        [
            {
                "kind": "radiance.metrics",
                "metadata": {"files": [{"name": "x", "uri": "mem://x", "sha256": "A" * 64}]},
            }
        ],
        [{"kind": "radiance.metrics", "files": [{}]}],
        [{"kind": "radiance.metrics", "files": [{"name": "x", "uri": "mem://x", "byte_size": -1}]}],
    ],
)
def test_radiance_train_rejects_malformed_nested_evaluation_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    artifacts: Any,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "succeeded",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "snapshot_seq": 7,
                "snapshot_path": "mem://snapshots/rf-provider/7",
                "evaluations": [
                    {
                        "radiance_field_id": "rf-provider",
                        "evaluation_id": "ev-task",
                        "metrics": {"psnr": 31.0},
                        "artifacts": artifacts,
                    }
                ],
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-task",
                    "evaluation_id": "ev-task",
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.train.default",
                    "eval": {"enabled": True},
                },
            },
        )

        with pytest.raises(ValidationError):
            run(task)  # type: ignore[arg-type]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_eval_overwrites_provider_echoed_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "succeeded",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "evaluation_id": "ev-provider",
                "metrics": {"psnr": 31.0},
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-task",
                    "evaluation_id": "ev-task",
                    "snapshot_seq": 3,
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.evaluate.default",
                },
            },
        )

        outputs = run_eval(task)  # type: ignore[arg-type]

        assert captured["body"]["task_kind"] == "radiance_eval"
        assert outputs["radiance_field_id"] == "rf-task"
        assert outputs["evaluation_id"] == "ev-task"
        assert outputs["metrics"] == {"psnr": 31.0}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_eval_rejects_nested_metric_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {
        "response": {
            "status": "succeeded",
            "outputs": {
                "radiance_field_id": "rf-provider",
                "evaluation_id": "ev-provider",
                "metrics": {"psnr": 31.0},
                "evaluations": [
                    {
                        "evaluation_id": "ev-task",
                        "metrics": {"psnr": 29.0},
                    }
                ],
            },
        }
    }
    server, thread, base_url = _start_execute_service(captured)
    try:
        monkeypatch.setenv("SFMAPI_GSPLAT_SERVICE_URL", base_url)
        record_manual_install("gsplat", method="container_service", enabled=True)
        task = SimpleNamespace(
            tenant_id="tenant-1",
            job_id="job-1",
            task_id="task-1",
            task_state_json={
                "inputs": {
                    "project_id": "project-1",
                    "radiance_field_id": "rf-task",
                    "evaluation_id": "ev-task",
                    "snapshot_seq": 3,
                },
                "spec": {
                    "provider": "gsplat",
                    "method": "gsplat.evaluate.default",
                },
            },
        )

        with pytest.raises(ValidationError):
            run_eval(task)  # type: ignore[arg-type]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_radiance_train_requires_installed_enabled_plugin_provider() -> None:
    task = SimpleNamespace(
        tenant_id="tenant-1",
        job_id="job-1",
        task_id="task-1",
        task_state_json={
            "inputs": {
                "project_id": "project-1",
                "radiance_field_id": "rf-1",
            },
            "spec": {
                "provider": "gsplat",
                "method": "gsplat.train.default",
            },
        },
    )

    with pytest.raises(CapabilityUnavailableError, match="not installed and enabled"):
        run(task)  # type: ignore[arg-type]


def test_stub_radiance_train_cleans_live_directory(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePaths:
        def radiance_field_root(
            self,
            tenant_id: str,
            project_id: str,
            radiance_field_id: str,
        ):
            return tmp_path / tenant_id / project_id / radiance_field_id

    import sceneapi.server.workers.tasks.radiance_train as module

    monkeypatch.setattr(module, "Paths", FakePaths)
    task = SimpleNamespace(
        tenant_id="tenant-1",
        job_id="job-1",
        task_id="task-1",
        task_state_json={
            "inputs": {
                "project_id": "project-1",
                "radiance_field_id": "rf-1",
                "dataset_id": "dataset-1",
            },
            "spec": {"provider": "stub", "method": "stub"},
        },
    )

    outputs = run(task)  # type: ignore[arg-type]

    root = tmp_path / "tenant-1" / "project-1" / "rf-1"
    assert (root / "snapshots" / "00000001" / "point_cloud.ply").is_file()
    assert not (root / "_live" / "task-1").exists()
    assert outputs["snapshot_seq"] == 1
