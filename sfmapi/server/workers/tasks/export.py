"""Export a reconstruction to PLY / NVM / COLMAP text/binary / ..."""

from __future__ import annotations

from pathlib import Path

from sfmapi.server.adapters.backend import require_backend_method
from sfmapi.server.db.models import Task
from sfmapi.server.workers._task_io import read_state, stage_output_dir
from sfmapi.server.workers.backend_resolver import backend_for_stage
from sfmapi.server.workers.tasks._registry import task_handler


@task_handler("export")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    fmt = spec.get("format", "ply")
    backend = backend_for_stage(spec)
    export = require_backend_method(
        backend,
        "export",
        capability=f"export.{fmt}",
    )
    return export(
        model_path=Path(inputs["model_path"]),
        output_path=stage_output_dir(root=inputs["reconstruction_root"], task=task, name="export"),
        format=fmt,
    )
