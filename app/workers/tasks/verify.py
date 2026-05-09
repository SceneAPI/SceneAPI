"""Geometric verification task."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from app.adapters.progress import call_with_optional_progress
from app.adapters.registry import get_backend
from app.db.models import Task
from app.storage.two_view_emit import export_two_view_geometries
from app.workers._task_io import read_state
from app.workers.options import stage_options
from app.workers.progress import get_progress_reporter
from app.workers.tasks._registry import task_handler


@task_handler("verify")
def run(task: Task) -> dict[str, Any]:
    inputs, spec = read_state(task)
    db_path = Path(inputs["database_path"])
    backend = get_backend()
    progress = get_progress_reporter()
    if progress is not None:
        progress.phase_started("geometric_verification")
    summary = call_with_optional_progress(
        backend.verify_matches,
        progress=progress,
        database_path=db_path,
        options=stage_options(spec),
    )
    if progress is not None:
        progress.phase_completed("geometric_verification")

    # Export the verified two-view geometries as a wire-stable JSON sidecar
    # next to the database. Best-effort: failure here doesn't fail verify.
    out: dict[str, Any] = {"database_path": str(db_path), **summary}
    with contextlib.suppress(Exception):
        written = export_two_view_geometries(
            backend.iter_two_view_geometries(database_path=db_path), db_path.parent
        )
        out["two_view_geometries_path"] = str(written)
    return out
