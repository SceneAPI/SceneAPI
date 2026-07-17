from __future__ import annotations

import pytest

from sfmapi.server.services.sfm_stage_service import build_recipe_dag

pytestmark = pytest.mark.unit


def _recipe_nodes():
    return build_recipe_dag(
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


def _recipe_hashes() -> dict[str, str]:
    return {node.kind: node.inputs_hash for node in _recipe_nodes()}


def test_recipe_stage_cache_hashes_do_not_include_generated_task_ids() -> None:
    assert _recipe_hashes() == _recipe_hashes()


def test_stage_node_cache_hashes_are_pinned() -> None:
    """Cache-key parity guard: `(inputs_hash, params_hash)` per stage
    for a fixed recipe input, pinned across refactors.

    The expectations below were computed from the pre-split monolithic
    ``sfm_stage_service`` (lean audit 2026-07, item 3.4) and must never
    change silently: these hashes are persisted cache keys, so any drift
    invalidates every existing task cache. If a deliberate cache-format
    change is made, bump the expectations in the same commit that
    documents the invalidation.

    extract/map share an inputs_hash (same key/value set under
    canonical JSON), as do match/verify — that overlap is expected.
    """
    expected = {
        "extract": (
            "09d4925fba8c69f19e3c3795c34febded143cdaf863813866b4eb92e1789712c",
            "d689562d3c87bec1c398a27cc8094422eacb79439a96bcc7630e9d797d66c50e",
        ),
        "match": (
            "f0c425b14acd043a4dd1aa9de3b70a2d0ab04afb059fc880fe7e882d6f010edf",
            "6e6066aa2ae8fa388e51d8896ad7f09a5372a2aeb3d4a421f772398c7b4650d9",
        ),
        "verify": (
            "f0c425b14acd043a4dd1aa9de3b70a2d0ab04afb059fc880fe7e882d6f010edf",
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
        ),
        "map": (
            "09d4925fba8c69f19e3c3795c34febded143cdaf863813866b4eb92e1789712c",
            "25f383725a2a13a108ae10456d9178d78341d19ee709d70020afb4438d806dec",
        ),
    }
    actual = {node.kind: (node.inputs_hash, node.params_hash) for node in _recipe_nodes()}
    assert actual == expected
