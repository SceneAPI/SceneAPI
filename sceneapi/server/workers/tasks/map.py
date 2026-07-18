"""Mapping task — incremental | global | hierarchical | spherical | feed_forward.

Dual dispatch: a backend that implements the sceneapi-io ``Mapper``
contract is preferred (``sceneapi.server.workers._io_map`` bridges the
materialized image set into ``ViewInput``s and the ``MappingResult``
back into the existing snapshot emission path); otherwise the v0
``backend.run_mapping(kind=...)`` protocol runs unchanged, followed by
the sfmapi-side post-processing: per-submodel snapshot emit, primary-
submodel emit at the flat ``sparse/`` root for the legacy snapshot
read endpoint, and a sealed snapshot. ``kind="feed_forward"`` has no v0
form — without an io Mapper it is an honest 501 (``map.feed_forward``).

Resume support is internal to the backend — the colmap_mod backend
writes ``MappingInput`` checkpoints into ``jobs/{job_id}/checkpoints/``
and resumes from the latest one when the same task re-runs. Other
backends may use their own checkpoint format; the interface here just
threads ``job_dir`` through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from sceneapi.server.adapters.backend import require_backend_method
from sceneapi.server.adapters.progress import call_with_optional_progress
from sceneapi.server.core.config import get_settings
from sceneapi.server.core.errors import CapabilityUnavailableError, ValidationError
from sceneapi.server.core.paths import Paths
from sceneapi.server.db.models import Task
from sceneapi.server.schemas.progress_event import Phase
from sceneapi.server.services import reconstruction_service
from sceneapi.server.storage.snapshot_emit import emit_snapshot_files
from sceneapi.server.storage.snapshots import SnapshotStore
from sceneapi.server.workers._io_dispatch import io_mapper
from sceneapi.server.workers._io_map import run_io_mapping
from sceneapi.server.workers._materialize import materialize_image_set
from sceneapi.server.workers._task_io import read_state
from sceneapi.server.workers.backend_resolver import backend_for_stage
from sceneapi.server.workers.options import stage_options
from sceneapi.server.workers.progress import get_progress_reporter
from sceneapi.server.workers.tasks._registry import task_handler

MAPPING_PHASE_BY_KIND: dict[str, Phase] = {
    "incremental": "incremental_register",
    "global": "global_positioning",
    "hierarchical": "hierarchical_cluster",
    "spherical": "spherical",
    "feed_forward": "feed_forward",
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
    if kind not in MAPPING_PHASE_BY_KIND:
        raise ValidationError(f"Unknown mapping kind: {kind!r}")

    progress = get_progress_reporter()
    phase = MAPPING_PHASE_BY_KIND[kind]
    options = stage_options(spec)
    if inputs.get("input_artifacts"):
        options["input_artifacts"] = inputs["input_artifacts"]
    if progress is not None:
        progress.phase_started(phase)
    backend = backend_for_stage(spec)
    mapper = io_mapper(backend)
    if mapper is not None:
        # Preferred path: the backend implements the neutral sceneapi-io
        # Mapper contract. The bridge emits the snapshot files itself and
        # returns the one submodel summary (unregistered views recorded
        # in it); dense outputs land as job-dir files referenced from it.
        summaries = [
            run_io_mapping(
                mapper,
                kind=kind,
                image_root=image_root,
                image_list=list(materialization.get("image_list") or []),
                sparse_root=sparse_root,
                job_dir=job_dir,
                spec=spec,
                pose_priors=pose_priors,
                input_artifacts=inputs.get("input_artifacts"),
            )
        ]
    elif kind == "feed_forward":
        # No v0 protocol expresses feed-forward mapping: run_mapping's
        # kind vocabulary is the classical family. Honest 501 rather
        # than handing an engine adapter a kind it never defined.
        raise CapabilityUnavailableError(
            capability="map.feed_forward",
            reason=(
                "feed-forward mapping requires a backend implementing the "
                "sceneapi-io Mapper contract; the v0 run_mapping protocol "
                "has no feed-forward form"
            ),
        )
    else:
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
