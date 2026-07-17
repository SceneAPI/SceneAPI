"""Standalone bundle adjustment — produces a refined model revision.

``spec.mode`` (see :class:`sceneapi.server.schemas.pipeline_spec.BundleAdjustmentSpec`)
selects the algorithm and the gating capability:

  - ``standard``      → ``ba.standard`` (default).
  - ``two_stage``     → ``ba.two_stage`` (poses-only pass, then full unlock).
  - ``featuremetric`` → ``ba.featuremetric`` (CNN-feature error).
  - ``rig``           → ``ba.rig`` (multi-camera rig refinement).
"""

from __future__ import annotations

from pathlib import Path

from sceneapi.server.adapters.backend import require_backend_method
from sceneapi.server.core.capabilities import require as require_capability
from sceneapi.server.db.models import Task
from sceneapi.server.schemas.pipeline_spec import BA_MODE_CAPABILITIES
from sceneapi.server.workers._task_io import read_state, stage_output_dir
from sceneapi.server.workers.backend_resolver import backend_for_stage
from sceneapi.server.workers.options import stage_options
from sceneapi.server.workers.tasks._registry import task_handler


@task_handler("ba")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    mode = (spec.get("mode") or "standard").lower()
    capability = BA_MODE_CAPABILITIES.get(mode, "ba.standard")
    if capability != "ba.standard":
        require_capability(capability)
    backend = backend_for_stage(spec)
    bundle_adjustment = require_backend_method(
        backend,
        "bundle_adjustment",
        capability=capability,
    )
    return bundle_adjustment(
        model_path=Path(inputs["model_path"]),
        output_path=stage_output_dir(root=inputs["reconstruction_root"], task=task, name="ba"),
        spec=stage_options(spec),
    )
