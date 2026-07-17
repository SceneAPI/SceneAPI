"""Re-triangulation against an existing database."""

from __future__ import annotations

from pathlib import Path

from sceneapi.server.adapters.backend import require_backend_method
from sceneapi.server.db.models import Task
from sceneapi.server.workers._task_io import read_state, stage_output_dir
from sceneapi.server.workers.backend_resolver import backend_for_stage
from sceneapi.server.workers.tasks._registry import task_handler


@task_handler("triangulate")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    backend = backend_for_stage(spec)
    triangulate = require_backend_method(
        backend,
        "triangulate",
        capability="triangulate.retri",
    )
    return triangulate(
        model_path=Path(inputs["model_path"]),
        database_path=Path(inputs["database_path"]),
        image_root=Path(inputs["image_root"]),
        output_path=stage_output_dir(
            root=inputs["reconstruction_root"], task=task, name="triangulate"
        ),
    )
