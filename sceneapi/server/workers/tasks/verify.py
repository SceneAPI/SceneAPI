"""Geometric verification task."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from sceneapi.server.adapters.backend import require_backend_method
from sceneapi.server.adapters.progress import call_with_optional_progress
from sceneapi.server.core.logging import get_logger
from sceneapi.server.db.models import Task
from sceneapi.server.storage.two_view_emit import export_two_view_geometries
from sceneapi.server.workers._io_dispatch import io_geometric_verifier
from sceneapi.server.workers._io_match import run_io_verify
from sceneapi.server.workers._task_io import read_state
from sceneapi.server.workers.backend_resolver import backend_for_stage
from sceneapi.server.workers.options import stage_options
from sceneapi.server.workers.progress import get_progress_reporter
from sceneapi.server.workers.tasks._registry import task_handler

_log = get_logger("sceneapi.workers.tasks.verify")


@task_handler("verify")
def run(task: Task) -> dict[str, Any]:
    inputs, spec = read_state(task)
    db_path = Path(inputs["database_path"])
    backend = backend_for_stage(spec)
    verifier = io_geometric_verifier(backend)
    if verifier is not None:
        # Preferred path (P8 Step 6): the backend implements the neutral
        # sceneapi-io GeometricVerifier contract. Filter every pair the io
        # matcher persisted and write the geometrically-consistent subset
        # (+ TwoViewGeometry) into the io correspondence store the map
        # stage reads via the shared database_path anchor.
        progress = get_progress_reporter()
        if progress is not None:
            progress.phase_started("geometric_verification")
        out = run_io_verify(
            verifier,
            backend=backend,
            db_path=db_path,
            spec=spec,
            progress=progress,
        )
        if progress is not None:
            progress.phase_completed("geometric_verification")
        return out
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
