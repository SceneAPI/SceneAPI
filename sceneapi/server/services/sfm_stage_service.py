"""Build the Job -> Task DAG for SfM stage calls and named recipes.

The HTTP layer no longer needs to pass `image_root` / `image_list` /
`database_path` — they are derived here from the dataset's source and
the persisted Image rows. This keeps the API surface clean (the
client knows about its dataset, not about worker-side filesystem
layout) and ensures the same materialization logic is used for every
stage.

A *recipe* (`incremental | global | hierarchical | spherical`) is
sugar over the per-stage builders: it strings extract → match →
verify → map into one DAG so per-stage caching short-circuits as
soon as any prefix is reused.

This module is the stable facade over the domain-split implementation
(lean audit 2026-07, item 3.4). Callers keep importing
``sceneapi.server.services.sfm_stage_service``; the code lives in:

- ``_sfm_stage_core``    — ``_stage_node`` / ``_submit_single_stage``
  (the cache-key-bearing primitives, decision register L30) plus the
  materialization / path / spec helpers shared by every stage.
- ``_sfm_stage_dataset`` — dataset-scoped stages: features / matches /
  verify + their validators, projection & import utilities, and the
  table-driven feature-database stages.
- ``_sfm_stage_recon``   — reconstruction-scoped stages: the
  table-driven portable stages (ba / triangulate / pgo / export /
  relocalize / undistort) plus localize / merge / to_cubemap /
  georegister.
- ``_sfm_stage_recipes`` — ``build_recipe_dag`` + recipe-wide config
  validation + pose-prior collection.

Cache-key parity contract: a single-stage submission and the same stage
inside a recipe must produce identical ``(inputs_hash, params_hash)``
(pinned by ``tests/unit/test_stage_dag.py``), so all hashing funnels
through ``_sfm_stage_core._stage_node``.
"""

from __future__ import annotations

from sceneapi.server.services._sfm_stage_core import (
    _merge_spec_input_artifacts as _merge_spec_input_artifacts,
)
from sceneapi.server.services._sfm_stage_core import (
    _reconstruction_paths as _reconstruction_paths,
)
from sceneapi.server.services._sfm_stage_core import (
    _resolve_database_path as _resolve_database_path,
)
from sceneapi.server.services._sfm_stage_core import (
    _routing_workspace as _routing_workspace,
)
from sceneapi.server.services._sfm_stage_core import (
    _stage_backend_options as _stage_backend_options,
)
from sceneapi.server.services._sfm_stage_core import (
    _stage_node as _stage_node,
)
from sceneapi.server.services._sfm_stage_core import (
    _submit_single_stage as _submit_single_stage,
)
from sceneapi.server.services._sfm_stage_core import (
    derive_materialization,
    ensure_reconstruction,
    reconstruction_database_path,
)
from sceneapi.server.services._sfm_stage_dataset import (
    _submit_dataset_db_stage as _submit_dataset_db_stage,
)
from sceneapi.server.services._sfm_stage_dataset import (
    _validate_explicit_pairs as _validate_explicit_pairs,
)
from sceneapi.server.services._sfm_stage_dataset import (
    submit_build_vocab_tree,
    submit_configure_rig,
    submit_dataset_from_archive,
    submit_estimate_two_view,
    submit_features,
    submit_kapture_import,
    submit_matches,
    submit_project_images,
    submit_render_cubemap,
    submit_verify,
    submit_video_frames,
    submit_vlad_index,
    validate_features_config,
    validate_mapping_config,
    validate_matches_config,
    validate_verify_config,
)
from sceneapi.server.services._sfm_stage_recipes import (
    build_recipe_dag,
    collect_pose_priors_by_name,
    validate_recipe_stage_configs,
)
from sceneapi.server.services._sfm_stage_recon import (
    BA_MODE_CAPABILITIES,
    submit_bundle_adjust,
    submit_export,
    submit_georegister,
    submit_localize,
    submit_merge_recons,
    submit_pose_graph_optimize,
    submit_relocalize,
    submit_to_cubemap,
    submit_triangulate,
    submit_undistort,
)
from sceneapi.server.services._sfm_stage_recon import (
    _bundle_adjust_capability as _bundle_adjust_capability,
)
from sceneapi.server.services._sfm_stage_recon import (
    _recon_stage_base as _recon_stage_base,
)
from sceneapi.server.services._sfm_stage_recon import (
    _submit_recon_stage as _submit_recon_stage,
)

__all__ = [
    "BA_MODE_CAPABILITIES",
    "build_recipe_dag",
    "collect_pose_priors_by_name",
    "derive_materialization",
    "ensure_reconstruction",
    "reconstruction_database_path",
    "submit_build_vocab_tree",
    "submit_bundle_adjust",
    "submit_configure_rig",
    "submit_dataset_from_archive",
    "submit_estimate_two_view",
    "submit_export",
    "submit_features",
    "submit_georegister",
    "submit_kapture_import",
    "submit_localize",
    "submit_matches",
    "submit_merge_recons",
    "submit_pose_graph_optimize",
    "submit_project_images",
    "submit_relocalize",
    "submit_render_cubemap",
    "submit_to_cubemap",
    "submit_triangulate",
    "submit_undistort",
    "submit_verify",
    "submit_video_frames",
    "submit_vlad_index",
    "validate_features_config",
    "validate_mapping_config",
    "validate_matches_config",
    "validate_recipe_stage_configs",
    "validate_verify_config",
]
