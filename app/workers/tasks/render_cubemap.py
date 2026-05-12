"""Render every spherical (equirectangular) image in a dataset into
six undistorted cubemap faces.

The image-level sibling of ``to_cubemap`` (which runs on the
*reconstruction*). The output lives at
``<dataset_root>/_cubemap/<task_id>/``; the user can register that
directory as a new ``local`` dataset for downstream pinhole-only
pipelines.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

from app.adapters.backend import require_backend_method
from app.adapters.registry import get_backend
from app.core.config import get_settings
from app.core.paths import Paths
from app.db.models import Task
from app.workers._materialize import materialize_image_set
from app.workers._task_io import read_state
from app.workers.tasks._registry import task_handler


@task_handler("render_cubemap")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    materialization = inputs["materialization"]
    dataset_dir = Path(inputs["dataset_dir"])
    face_size = spec.get("face_size")

    paths = Paths(get_settings())
    stage = paths.workspace_root / "_cubemap_stage" / task.task_id
    image_path, _ = materialize_image_set(materialization, stage)

    output_path = dataset_dir / "_cubemap" / task.task_id
    output_path.mkdir(parents=True, exist_ok=True)

    render_spherical_cubemap_images = require_backend_method(
        get_backend(),
        "render_spherical_cubemap_images",
        capability="projection.equirectangular_to_cubemap",
    )
    render_spherical_cubemap_images(
        input_image_path=image_path,
        output_path=output_path,
        face_size=face_size,
    )

    rendered_files = [p for p in sorted(output_path.rglob("*")) if p.is_file()]
    with contextlib.suppress(OSError):
        shutil.rmtree(stage)

    return {
        "output_path": str(output_path),
        "num_files": len(rendered_files),
        "face_size": face_size,
    }
