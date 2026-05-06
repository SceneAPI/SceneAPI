"""Build a VLAD descriptor index for a dataset.

Materializes every image to a local path, then dispatches to
``backend.build_vlad_index(image_paths_by_id, spec)``. The backend
returns L2-normalizable vectors that the worker persists via
:mod:`app.storage.vlad` so the web tier can query without the
backend installed.

The task intentionally re-extracts SIFT (or whatever the backend uses)
rather than reading an existing engine database — so VLAD can be built
before the user has run ``extract``.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

from app.adapters.registry import get_backend
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.paths import Paths
from app.db.models import Task
from app.storage.vlad import write_index as _write_vlad_index
from app.workers._materialize import resolve_image_path
from app.workers._task_io import read_state


def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    materialization = inputs["materialization"]
    image_id_by_name: dict[str, str] = inputs.get("image_id_by_name") or {}
    dataset_dir = Path(inputs["dataset_dir"])
    manifest_hash = str(inputs.get("manifest_hash") or "")
    if not image_id_by_name:
        raise ValidationError("vlad_index: image_id_by_name is required")

    paths = Paths(get_settings())
    stage = paths.workspace_root / "_vlad_stage" / task.task_id
    stage.mkdir(parents=True, exist_ok=True)

    image_names: list[str] = list(materialization.get("image_list") or [])
    image_paths_by_id: dict[str, Path] = {}
    for name in image_names:
        sfmapi_id = image_id_by_name.get(name)
        if sfmapi_id is None:
            continue
        path = resolve_image_path(name, materialization, stage)
        if path is None or not path.is_file():
            continue
        image_paths_by_id[sfmapi_id] = path

    if not image_paths_by_id:
        raise ValidationError("vlad_index: no images could be materialized for VLAD build")

    sfmapi_ids, vectors = get_backend().build_vlad_index(
        image_paths_by_id=image_paths_by_id, spec=spec
    )
    if vectors.size == 0:
        raise ValidationError(
            "vlad_index: backend returned no descriptors (SIFT extraction failed for every image)"
        )
    out_path = _write_vlad_index(
        dataset_dir,
        image_ids=sfmapi_ids,
        vectors=vectors,
        manifest_hash=manifest_hash,
    )
    with contextlib.suppress(OSError):
        shutil.rmtree(stage)
    return {
        "vlad_path": str(out_path),
        "count": len(sfmapi_ids),
        "dim": int(vectors.shape[1]) if vectors.ndim == 2 else 0,
    }
