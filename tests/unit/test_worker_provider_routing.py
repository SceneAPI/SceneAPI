from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sfmapi.server.adapters.registry import register_backend
from sfmapi.server.db.models import Task
from sfmapi.server.workers.tasks import extract as extract_task

pytestmark = pytest.mark.unit


def test_extract_worker_uses_resolved_provider_backend(tmp_path: Path) -> None:
    calls: list[str] = []

    class DefaultBackend:
        name = "default_backend"
        version = "1"
        vendor = "tests"

        def capabilities(self) -> set[str]:
            return {"features.extract.sift"}

        def runtime_versions(self) -> dict[str, str]:
            return {"default": "1"}

        def extract_features(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(self.name)
            return {"backend": self.name, "num_images": len(kwargs["image_list"])}

    class ProviderBackend(DefaultBackend):
        name = "provider_backend"

    register_backend("default_backend", DefaultBackend)
    register_backend("provider_backend", ProviderBackend, providers=["provider.features"])

    image_root = tmp_path / "images"
    image_root.mkdir()
    (image_root / "a.jpg").write_bytes(b"fake")
    db_path = tmp_path / "database.db"
    task = Task(
        task_id="01H00000000000000000000000",
        tenant_id="default",
        job_id="01H00000000000000000000001",
        kind="extract",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        task_state_json={
            "inputs": {
                "project_id": "project-1",
                "recon_id": "recon-1",
                "database_path": str(db_path),
                "materialization": {
                    "kind": "local",
                    "image_root": str(image_root),
                    "image_list": ["a.jpg"],
                },
            },
            "spec": {"type": "sift", "provider": "provider.features"},
        },
    )

    out = extract_task.run(task)

    assert calls == ["provider_backend"]
    assert out["backend"] == "provider_backend"
    assert out["artifacts"][0]["producer"] == {"backend": "provider_backend"}
