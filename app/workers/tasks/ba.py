"""Standalone bundle adjustment — produces a refined model revision.

``spec.mode`` (see :class:`app.schemas.pipeline_spec.BundleAdjustmentSpec`)
selects the algorithm and the gating capability:

  - ``standard``      → ``ba.standard`` (default).
  - ``two_stage``     → ``ba.two_stage`` (poses-only pass, then full unlock).
  - ``featuremetric`` → ``ba.featuremetric`` (CNN-feature error).
  - ``rig``           → ``ba.rig`` (multi-camera rig refinement).
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.backend import require_backend_method
from app.core.capabilities import require as require_capability
from app.db.models import Task
from app.schemas.pipeline_spec import BA_MODE_CAPABILITIES
from app.workers._task_io import read_state, stage_output_dir
from app.workers.backend_resolver import backend_for_stage
from app.workers.options import stage_options
from app.workers.tasks._registry import task_handler


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
