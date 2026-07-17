"""Standalone two-view geometry estimation for a dataset.

Capability ``geometry.two_view``. Distinct from the bundled ``verify``
stage (which filters an existing match set in place): this estimates
relative geometry — E/F/H matrices and relative pose — for image pairs
in the dataset's feature database.
"""

from __future__ import annotations

from pathlib import Path

from sfmapi.server.adapters.backend import require_backend_method
from sfmapi.server.db.models import Task
from sfmapi.server.workers._task_io import read_state
from sfmapi.server.workers.backend_resolver import backend_for_stage
from sfmapi.server.workers.options import stage_options
from sfmapi.server.workers.tasks._registry import task_handler


@task_handler("two_view")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    backend = backend_for_stage(spec)
    estimate_two_view_geometry = require_backend_method(
        backend,
        "estimate_two_view_geometry",
        capability="geometry.two_view",
    )
    return estimate_two_view_geometry(
        database_path=Path(inputs["database_path"]),
        spec=stage_options(spec),
    )
