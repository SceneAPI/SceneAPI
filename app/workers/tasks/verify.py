"""Geometric verification task."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from app.adapters.backend import require_backend_method
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
    options = stage_options(spec)
    if inputs.get("input_artifacts"):
        options["input_artifacts"] = inputs["input_artifacts"]
    verify_matches = require_backend_method(
        backend,
        "verify_matches",
        capability="matches.verify",
    )
    progress = get_progress_reporter()
    if progress is not None:
        progress.phase_started("geometric_verification")
    summary = call_with_optional_progress(
        verify_matches,
        progress=progress,
        database_path=db_path,
        options=options,
    )
    if progress is not None:
        progress.phase_completed("geometric_verification")

    # Export the verified two-view geometries as a wire-stable JSON sidecar
    # next to the database. Best-effort: failure here doesn't fail verify.
    backend_name = str(getattr(backend, "name", "unknown"))
    artifacts: list[dict[str, Any]] = [
        {
            "kind": f"matches.database.verified.{backend_name}",
            "name": "verified-match-database",
            "uri": str(db_path),
            "summary": summary if isinstance(summary, dict) else {},
            "artifact_format": f"{backend_name}.matches.database.verified.v1",
            "schema_version": 1,
            "producer": {"backend": backend_name},
        }
    ]
    out: dict[str, Any] = {"database_path": str(db_path), **summary, "artifacts": artifacts}
    with contextlib.suppress(Exception):
        iter_two_view_geometries = require_backend_method(
            backend,
            "iter_two_view_geometries",
            capability="observations.by_image",
        )
        written = export_two_view_geometries(
            iter_two_view_geometries(database_path=db_path), db_path.parent
        )
        out["two_view_geometries_path"] = str(written)
        artifacts.append(
            {
                "kind": "matches.verified.v1",
                "name": "two_view_geometries",
                "uri": str(written),
                "media_type": "application/json",
                "artifact_format": "sfmapi.matches.verified.v1",
                "schema_version": 1,
                "producer": {"backend": backend_name},
            }
        )
    return out
