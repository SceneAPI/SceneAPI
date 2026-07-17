"""Undistort a reconstruction's images + emit adjusted intrinsics.

A portable sparse-SfM post-process (capability ``image.undistort``):
rewrite images to a distortion-free camera model. NOT dense MVS —
though it is commonly the first step of a dense pipeline.
"""

from __future__ import annotations

from pathlib import Path

from sceneapi.server.adapters.backend import require_backend_method
from sceneapi.server.db.models import Task
from sceneapi.server.workers._task_io import read_state, stage_output_dir
from sceneapi.server.workers.backend_resolver import backend_for_stage
from sceneapi.server.workers.options import stage_options
from sceneapi.server.workers.tasks._registry import task_handler


@task_handler("undistort")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    backend = backend_for_stage(spec)
    undistort_images = require_backend_method(
        backend,
        "undistort_images",
        capability="image.undistort",
    )
    return undistort_images(
        model_path=Path(inputs["model_path"]),
        image_root=Path(inputs["image_root"]),
        output_path=stage_output_dir(
            root=inputs["reconstruction_root"], task=task, name="undistort"
        ),
        spec=stage_options(spec),
    )
