"""Merge multiple reconstructions into one.

Calls ``backend.merge_reconstructions`` and seals the result as a
fresh snapshot under the *target* reconstruction's workspace.
"""

from __future__ import annotations

from pathlib import Path

from sceneapi.server.adapters.backend import require_backend_method
from sceneapi.server.core.errors import ValidationError
from sceneapi.server.db.models import Task
from sceneapi.server.storage.snapshots import SnapshotStore
from sceneapi.server.workers._task_io import read_state
from sceneapi.server.workers.backend_resolver import backend_for_stage
from sceneapi.server.workers.tasks._registry import task_handler


@task_handler("merge_recons")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    target_root = Path(inputs["target_reconstruction_root"])
    source_sparse_dirs = [Path(p) for p in inputs.get("source_sparse_dirs") or []]
    if len(source_sparse_dirs) < 2:
        raise ValidationError("merge: at least two source reconstructions required")
    sim3_aligners = spec.get("sim3_aligners")

    out_dir = target_root / "_merged" / task.task_id
    out_dir.mkdir(parents=True, exist_ok=True)

    backend = backend_for_stage(spec)
    merge_reconstructions = require_backend_method(
        backend,
        "merge_reconstructions",
        capability="recon.merge",
    )
    summary = merge_reconstructions(
        model_paths=source_sparse_dirs,
        output_path=out_dir,
        sim3_aligners=sim3_aligners,
    )

    snapshots = SnapshotStore(target_root)
    seq = (snapshots.latest() or 0) + 1
    sealed = snapshots.seal(
        seq=seq,
        source_dir=out_dir,
        summary={"phase": "merge", "sources": [str(p) for p in source_sparse_dirs], **summary},
    )
    return {
        "snapshot_seq": seq,
        "snapshot_path": str(sealed),
        **summary,
    }
