"""Feature extraction task.

Materializes the dataset's images, ensures a `database.db`, calls
`pycolmap.extract_features`, returns a result reference. Sealed snapshot
emission is handled by `app.storage.snapshots.SnapshotStore` and is
optional for this stage (DB-only mutation).

The materialization step is what lets the API stay clean: the HTTP
caller only has to know the dataset_id; this task reads the
`materialization` blob the orchestrator put together (kind + image_list
+ blob_shas / image_root / s3 coords) and produces a real local
directory pycolmap can read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.backend import require_backend_method
from app.adapters.progress import call_with_optional_progress
from app.adapters.registry import get_backend
from app.core.config import get_settings
from app.core.paths import Paths
from app.db.models import Task
from app.workers._materialize import materialize_image_set
from app.workers._task_io import read_state
from app.workers.options import stage_options
from app.workers.progress import get_progress_reporter
from app.workers.tasks._registry import task_handler


def _materialize(
    task: Task,
    materialization: dict[str, Any],
    paths: Paths,
) -> tuple[Path, list[str]]:
    """Realize the dataset's images under a per-task stage dir."""
    stage = paths.workspace_root / "_stage" / task.task_id
    return materialize_image_set(materialization, stage)


@task_handler("extract")
def run(task: Task) -> dict[str, Any]:
    s = get_settings()
    paths = Paths(s)
    inputs, spec = read_state(task)
    project_id = inputs["project_id"]
    recon_id = inputs["recon_id"]
    materialization = inputs["materialization"]

    image_root, image_list = _materialize(task, materialization, paths)

    rec_root = paths.reconstruction_root(task.tenant_id, project_id, recon_id)
    rec_root.mkdir(parents=True, exist_ok=True)
    db_path = Path(inputs.get("database_path") or (rec_root / "database.db"))
    progress = get_progress_reporter()
    total_images = len(image_list)
    if progress is not None:
        progress.phase_started("feature_extraction")
        progress.phase_progress("feature_extraction", current=0, total=total_images)
    options = _feature_options(spec)
    if inputs.get("input_artifacts"):
        options["input_artifacts"] = inputs["input_artifacts"]
    backend = get_backend()
    feature_type = str(spec.get("type", "sift"))
    extract_features = require_backend_method(
        backend,
        "extract_features",
        capability=f"features.extract.{feature_type}",
    )
    summary = call_with_optional_progress(
        extract_features,
        progress=progress,
        database_path=db_path,
        image_root=image_root,
        image_list=image_list,
        options=options,
    )
    if progress is not None:
        progress.phase_progress("feature_extraction", current=total_images, total=total_images)
        progress.phase_completed("feature_extraction")
    backend_name = str(getattr(backend, "name", "unknown"))
    return {
        "database_path": str(db_path),
        **summary,
        "artifacts": [
            {
                "kind": f"features.database.{backend_name}",
                "name": "feature-database",
                "uri": str(db_path),
                "summary": summary if isinstance(summary, dict) else {},
                "artifact_format": f"{backend_name}.features.database.v1",
                "schema_version": 1,
                "producer": {"backend": backend_name},
            }
        ],
    }


def _feature_options(spec: dict[str, Any]) -> dict[str, Any]:
    options = stage_options(spec)
    if "sift" not in options:
        sift_options = {
            key: options[key] for key in ("max_num_features",) if options.get(key) is not None
        }
        options["sift"] = sift_options
    return options
