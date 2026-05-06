"""Apply a Sim(3) similarity transform to a reconstruction.

Calls ``backend.apply_sim3`` against the live ``sparse/`` model; the
backend writes the transformed sparse dir + the snapshot emit
sidecars. We then seal a fresh snapshot the same shape as
post-mapping snapshots so clients read ``cameras.json`` /
``images.json`` / ``points.bin`` from it directly.
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.registry import get_backend
from app.core.errors import ValidationError
from app.db.models import Task
from app.storage.snapshots import SnapshotStore
from app.workers._task_io import read_state


def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    rec_root = Path(inputs["reconstruction_root"])
    sparse_dir = Path(inputs["sparse_dir"])
    sim3_dict = spec.get("sim3") or inputs.get("sim3")
    if not sim3_dict:
        raise ValidationError("georegister: spec.sim3 is required")
    if not sparse_dir.is_dir():
        raise ValidationError(f"sparse dir does not exist: {sparse_dir}")

    out_dir = rec_root / "_georegister" / task.task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    get_backend().apply_sim3(model_path=sparse_dir, output_path=out_dir, sim3=sim3_dict)

    snapshots = SnapshotStore(rec_root)
    seq = (snapshots.latest() or 0) + 1
    sealed = snapshots.seal(
        seq=seq,
        source_dir=out_dir,
        summary={"phase": "georegister", "applied_sim3": sim3_dict},
    )
    return {
        "snapshot_seq": seq,
        "snapshot_path": str(sealed),
        "applied_sim3": sim3_dict,
    }
