"""Build a reusable vocabulary-tree retrieval index for a dataset.

Capability ``index.vocab_tree``. The resulting index is what
``pairs.vocabtree`` / ``pairs.retrieval`` then consume for pair
selection.
"""

from __future__ import annotations

from pathlib import Path

from sceneapi.server.adapters.backend import require_backend_method
from sceneapi.server.db.models import Task
from sceneapi.server.workers._task_io import read_state, stage_output_dir
from sceneapi.server.workers.backend_resolver import backend_for_stage
from sceneapi.server.workers.options import stage_options
from sceneapi.server.workers.tasks._registry import task_handler


@task_handler("vocab_tree")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    backend = backend_for_stage(spec)
    build_vocab_tree = require_backend_method(
        backend,
        "build_vocab_tree",
        capability="index.vocab_tree",
    )
    return build_vocab_tree(
        database_path=Path(inputs["database_path"]),
        output_path=stage_output_dir(root=inputs["dataset_dir"], task=task, name="vocab_tree"),
        spec=stage_options(spec),
    )
