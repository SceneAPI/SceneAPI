from __future__ import annotations

import pytest

from sfmapi.server.services.sfm_stage_service import build_recipe_dag

pytestmark = pytest.mark.unit


def _recipe_hashes() -> dict[str, str]:
    nodes = build_recipe_dag(
        project_id="project-a",
        dataset_id="dataset-a",
        recon_id="recon-a",
        materialization={"images": ["a.jpg"], "image_root": "C:/images"},
        database_path="C:/work/database.db",
        features_spec={"type": "sift"},
        matches_spec={
            "pairs": {"strategy": "exhaustive"},
            "matcher": {"type": "nn-mutual"},
        },
        verify_spec={},
        pipeline_spec={"kind": "incremental"},
    )
    return {node.kind: node.inputs_hash for node in nodes}


def test_recipe_stage_cache_hashes_do_not_include_generated_task_ids() -> None:
    assert _recipe_hashes() == _recipe_hashes()
