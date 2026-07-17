"""Georegister a reconstruction.

``spec.mode`` selects the path:

  - ``sim3`` (default): apply the caller-supplied ``spec.sim3``
    transform via ``backend.apply_sim3`` (capability ``georegister.sim3``).
  - ``gps``: solve + apply the transform from georeferenced inputs via
    ``backend.align_reconstruction`` (capability ``georegister.gps``).

Either way the backend writes a transformed sparse dir + the snapshot
emit sidecars; we then seal a fresh snapshot the same shape as
post-mapping snapshots so clients read ``cameras.json`` /
``images.json`` / ``points.bin`` from it directly.
"""

from __future__ import annotations

from pathlib import Path

from sfmapi.server.adapters.backend import require_backend_method
from sfmapi.server.core.errors import ValidationError
from sfmapi.server.db.models import Task
from sfmapi.server.storage.snapshots import SnapshotStore
from sfmapi.server.workers._task_io import read_state, stage_output_dir
from sfmapi.server.workers.backend_resolver import backend_for_stage
from sfmapi.server.workers.options import stage_options
from sfmapi.server.workers.tasks._registry import task_handler


@task_handler("georegister")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    rec_root = Path(inputs["reconstruction_root"])
    sparse_dir = Path(inputs["sparse_dir"])
    if not sparse_dir.is_dir():
        raise ValidationError(f"sparse dir does not exist: {sparse_dir}")
    mode = str(spec.get("mode") or "sim3")
    out_dir = stage_output_dir(root=rec_root, task=task, name="georegister")
    backend = backend_for_stage(spec)

    if mode == "gps":
        align_reconstruction = require_backend_method(
            backend,
            "align_reconstruction",
            capability="georegister.gps",
        )
        align_reconstruction(model_path=sparse_dir, output_path=out_dir, spec=stage_options(spec))
        summary: dict = {"phase": "georegister", "mode": "gps"}
        result: dict = {"mode": "gps"}
    else:
        sim3_dict = spec.get("sim3") or inputs.get("sim3")
        if not sim3_dict:
            raise ValidationError("georegister: spec.sim3 is required for mode='sim3'")
        apply_sim3 = require_backend_method(
            backend,
            "apply_sim3",
            capability="georegister.sim3",
        )
        apply_sim3(model_path=sparse_dir, output_path=out_dir, sim3=sim3_dict)
        summary = {"phase": "georegister", "applied_sim3": sim3_dict}
        result = {"mode": "sim3", "applied_sim3": sim3_dict}

    snapshots = SnapshotStore(rec_root)
    seq = (snapshots.latest() or 0) + 1
    sealed = snapshots.seal(seq=seq, source_dir=out_dir, summary=summary)
    result["snapshot_seq"] = seq
    result["snapshot_path"] = str(sealed)
    return result
