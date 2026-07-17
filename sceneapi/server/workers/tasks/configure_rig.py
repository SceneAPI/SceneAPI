"""Declare or calibrate a multi-camera rig over a dataset's feature DB.

Capability ``rigs.configure`` (COLMAP 3.10+ ``rig_configurator`` and
equivalents).
"""

from __future__ import annotations

from pathlib import Path

from sceneapi.server.adapters.backend import require_backend_method
from sceneapi.server.db.models import Task
from sceneapi.server.workers._task_io import read_state
from sceneapi.server.workers.backend_resolver import backend_for_stage
from sceneapi.server.workers.options import stage_options
from sceneapi.server.workers.tasks._registry import task_handler


@task_handler("configure_rig")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    backend = backend_for_stage(spec)
    configure_rig = require_backend_method(
        backend,
        "configure_rig",
        capability="rigs.configure",
    )
    return configure_rig(
        database_path=Path(inputs["database_path"]),
        spec=stage_options(spec),
    )
