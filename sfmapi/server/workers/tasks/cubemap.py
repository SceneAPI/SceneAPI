"""Convert a spherical reconstruction to a cubemap rig.

Calls ``backend.convert_spherical_to_cubemap``, then runs the snapshot
emitter on the new sparse model and seals a fresh snapshot whose
``rigs.json`` / ``frames.json`` carry the cubemap layout.
"""

from __future__ import annotations

from pathlib import Path

from sfmapi.server.adapters.backend import require_backend_method
from sfmapi.server.core.errors import ValidationError
from sfmapi.server.db.models import Task
from sfmapi.server.storage.snapshot_emit import emit_snapshot_files
from sfmapi.server.storage.snapshots import SnapshotStore
from sfmapi.server.workers._task_io import read_state
from sfmapi.server.workers.backend_resolver import backend_for_stage
from sfmapi.server.workers.tasks._registry import task_handler


@task_handler("to_cubemap")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    rec_root = Path(inputs["reconstruction_root"])
    sparse_dir = Path(inputs["sparse_dir"])
    image_root = Path(inputs["image_root"])
    if not sparse_dir.is_dir():
        raise ValidationError(f"sparse dir does not exist: {sparse_dir}")
    if not image_root.is_dir():
        raise ValidationError(f"image_root does not exist: {image_root}")

    out_dir = rec_root / "_cubemap" / task.task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = backend_for_stage(spec)
    convert_spherical_to_cubemap = require_backend_method(
        backend,
        "convert_spherical_to_cubemap",
        capability="projection.cubemap_rig",
    )
    read_reconstruction = require_backend_method(
        backend,
        "read_reconstruction",
        capability="projection.cubemap_rig",
        reason="Cubemap conversion needs read_reconstruction() to seal a snapshot.",
    )
    convert_spherical_to_cubemap(
        input_model_path=sparse_dir,
        input_image_path=image_root,
        output_path=out_dir,
    )
    rec = read_reconstruction(out_dir)
    emit_snapshot_files(rec, out_dir)

    snapshots = SnapshotStore(rec_root)
    seq = (snapshots.latest() or 0) + 1
    sealed = snapshots.seal(
        seq=seq,
        source_dir=out_dir,
        summary={"phase": "to_cubemap", "source_sparse_dir": str(sparse_dir)},
    )
    return {
        "snapshot_seq": seq,
        "snapshot_path": str(sealed),
        "cubemap_dir": str(out_dir),
    }
