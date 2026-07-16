"""Mapping task — incremental | global | hierarchical | spherical.

Dispatches to ``backend.run_mapping(kind=...)`` and then runs the
sfmapi-side post-processing: per-submodel snapshot emit, primary-
submodel emit at the flat ``sparse/`` root for the legacy snapshot
read endpoint, and a sealed snapshot.

Resume support is internal to the backend — the colmap_mod backend
writes ``MappingInput`` checkpoints into ``jobs/{job_id}/checkpoints/``
and resumes from the latest one when the same task re-runs. Other
backends may use their own checkpoint format; the interface here just
threads ``job_dir`` through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from app.adapters.backend import require_backend_method
from app.adapters.progress import call_with_optional_progress
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.paths import Paths
from app.db.models import Task
from app.schemas.progress_event import Phase
from app.services import reconstruction_service
from app.storage.snapshot_emit import emit_snapshot_files
from app.storage.snapshots import SnapshotStore
from app.workers._materialize import materialize_image_set
from app.workers._task_io import read_state
from app.workers.backend_resolver import backend_for_stage
from app.workers.options import stage_options
from app.workers.progress import get_progress_reporter
from app.workers.tasks._registry import task_handler

MAPPING_PHASE_BY_KIND: dict[str, Phase] = {
    "incremental": "incremental_register",
    "global": "global_positioning",
    "hierarchical": "hierarchical_cluster",
    "spherical": "spherical",
}


def _num_reg_images(rec: Any) -> int:
    """Backends may expose ``num_reg_images`` as a method (real
    pycolmap.Reconstruction) or as an attribute (test stubs)."""
    nr = getattr(rec, "num_reg_images", 0)
    return int(nr() if callable(nr) else nr)


def _task_recon_id(task: Task) -> str | None:
    state = task.task_state_json or {}
    inputs = state.get("inputs") or {}
    recon_id = inputs.get("recon_id")
    return str(recon_id) if recon_id else None


async def _on_status(session: Any, task: Task, status: str) -> None:
    """Roll the owning Reconstruction's status up with the task's."""
    recon_id = _task_recon_id(task)
    if recon_id is None:
        return
    await reconstruction_service.mark_reconstruction_status(
        session,
        tenant_id=task.tenant_id,
        recon_id=recon_id,
        status=status,
    )


async def _on_success(session: Any, task: Task, outputs: dict[str, Any]) -> None:
    """Persist submodel rows + snapshot pointers from mapping outputs."""
    result = outputs or {}
    recon_id = _task_recon_id(task)
    if recon_id is None:
        return
    models = result.get("models")
    if not isinstance(models, list):
        models = []
    model_summaries = [cast(dict[str, Any], m) for m in models if isinstance(m, dict)]
    await reconstruction_service.record_mapping_result(
        session,
        tenant_id=task.tenant_id,
        recon_id=recon_id,
        models=model_summaries,
        snapshot_seq=result.get("snapshot_seq"),
        snapshot_path=result.get("snapshot_path"),
    )


@task_handler("map", on_status=_on_status, on_success=_on_success)
def run(task: Task) -> dict[str, Any]:
    paths = Paths(get_settings())
    inputs, spec = read_state(task)
    project_id = inputs["project_id"]
    recon_id = inputs["recon_id"]
    db_path = Path(inputs["database_path"])
    materialization = inputs.get("materialization") or {}
    if "image_root" in inputs:
        image_root = Path(inputs["image_root"])
    else:
        stage = paths.workspace_root / "_stage" / task.task_id
        image_root, _image_list = materialize_image_set(materialization, stage)
    job_id = inputs.get("job_id") or task.job_id
    pose_priors = inputs.get("pose_priors") or {}

    rec_root = paths.reconstruction_root(task.tenant_id, project_id, recon_id)
    sparse_root = rec_root / "sparse"
    sparse_root.mkdir(parents=True, exist_ok=True)
    job_dir = paths.job_root(task.tenant_id, project_id, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    kind = spec.get("kind", "incremental")
    if kind not in ("incremental", "global", "hierarchical", "spherical"):
        raise ValidationError(f"Unknown mapping kind: {kind!r}")

    progress = get_progress_reporter()
    phase = MAPPING_PHASE_BY_KIND[kind]
    options = stage_options(spec)
    if inputs.get("input_artifacts"):
        options["input_artifacts"] = inputs["input_artifacts"]
    if progress is not None:
        progress.phase_started(phase)
    backend = backend_for_stage(spec)
    run_mapping = require_backend_method(
        backend,
        "run_mapping",
        capability=f"map.{kind}",
    )
    summaries, recs = call_with_optional_progress(
        run_mapping,
        progress=progress,
        kind=kind,
        db_path=db_path,
        image_root=image_root,
        sparse_root=sparse_root,
        job_dir=job_dir,
        spec=options,
        pose_priors=pose_priors,
    )

    # Convert each in-memory Reconstruction into the JSON+binary files
    # the API serves; the largest one is also written flat at sparse_root
    # so legacy `GET /snapshots/{seq}/{name}` callers see a sensible
    # default. Multi-submodel breakdown is preserved under sparse/<idx>/.
    if recs:
        if len(recs) == 1:
            emit_snapshot_files(recs[0], sparse_root)
        else:
            for idx, rec in enumerate(recs):
                emit_snapshot_files(rec, sparse_root / str(idx))
            primary = max(recs, key=_num_reg_images)
            emit_snapshot_files(primary, sparse_root)

    snapshots = SnapshotStore(rec_root)
    seq = (snapshots.latest() or 0) + 1
    sealed = snapshots.seal(seq=seq, source_dir=sparse_root, summary={"models": summaries})
    if progress is not None:
        progress.snapshot_available(snapshot_seq=seq, summary={"models": summaries})
        progress.phase_completed(phase)
    backend_name = str(getattr(backend, "name", "unknown"))
    return {
        "snapshot_seq": seq,
        "snapshot_path": str(sealed),
        "models": summaries,
        "job_dir": str(job_dir),
        "artifacts": [
            {
                "kind": "reconstruction.sparse.v1",
                "name": f"sparse-{seq}",
                "uri": str(sealed),
                "summary": {"snapshot_seq": seq, "models": summaries},
                "artifact_format": "sfmapi.reconstruction.sparse.v1",
                "schema_version": 1,
                "producer": {"backend": backend_name},
            },
            {
                "kind": "reconstruction.snapshot",
                "name": f"snapshot-{seq}",
                "uri": str(sealed),
                "summary": {"snapshot_seq": seq},
                "artifact_format": "sfmapi.reconstruction.snapshot.v1",
                "schema_version": 1,
                "producer": {"backend": backend_name},
            },
            *[
                {
                    "kind": "reconstruction.submodel",
                    "name": f"submodel-{summary.get('idx', position)}",
                    "uri": str(sealed / str(summary.get("idx", position)))
                    if len(summaries) > 1
                    else str(sealed),
                    "summary": summary,
                    "artifact_format": "sfmapi.reconstruction.submodel.v1",
                    "schema_version": 1,
                    "producer": {"backend": backend_name},
                }
                for position, summary in enumerate(summaries)
                if isinstance(summary, dict)
            ],
        ],
    }
