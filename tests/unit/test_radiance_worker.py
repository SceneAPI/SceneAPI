from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import CapabilityUnavailableError
from app.workers.tasks.radiance_train import run
from sfm_hub.state import record_manual_install

pytestmark = pytest.mark.unit


def _start_execute_service(
    captured: dict[str, Any],
) -> tuple[ThreadingHTTPServer, Thread, str]:
    class ExecuteHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            captured["path"] = self.path
            captured["body"] = json.loads(body.decode("utf-8"))
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
