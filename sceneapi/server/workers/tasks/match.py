"""Matching task — pair selection (PairsSpec) + per-pair matcher
(MatcherSpec).

The ``spec`` half of the task state carries ``{pairs: {...},
matcher: {...}}`` (the AIP-202 split shape). ``pairs.strategy``
selects which image pairs to consider; ``matcher.type`` selects the
per-pair algorithm."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from sceneapi.server.adapters.backend import require_backend_method
from sceneapi.server.adapters.progress import call_with_optional_progress
from sceneapi.server.core.config import get_settings
from sceneapi.server.core.errors import ValidationError
from sceneapi.server.core.logging import get_logger
from sceneapi.server.core.paths import Paths
from sceneapi.server.db.models import Task
from sceneapi.server.storage.blobs import get_blob_store
from sceneapi.server.storage.correspondence_emit import export_correspondence_graph
from sceneapi.server.workers._io_dispatch import io_pair_matcher
from sceneapi.server.workers._io_match import run_io_match
from sceneapi.server.workers._task_io import read_state
from sceneapi.server.workers.backend_resolver import backend_for_match_stage
from sceneapi.server.workers.progress import get_progress_reporter
from sceneapi.server.workers.tasks._registry import task_handler

_log = get_logger("sceneapi.workers.tasks.match")


def _pair_text_from_json_artifact(source: Path) -> str:
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"pairs artifact {source} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError(f"pairs artifact {source} must be a JSON object")
    raw_pairs = payload.get("pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise ValidationError(f"pairs artifact {source} requires a non-empty pairs array")
    lines: list[str] = []
    for index, item in enumerate(raw_pairs):
        if isinstance(item, dict):
            image_name1 = item.get("image_name1") or item.get("image1") or item.get("name1")
            image_name2 = item.get("image_name2") or item.get("image2") or item.get("name2")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            image_name1, image_name2 = item[0], item[1]
        else:
            raise ValidationError(f"pairs artifact {source} pairs[{index}] must be a two-item pair")
        image_name1 = str(image_name1 or "")
        image_name2 = str(image_name2 or "")
        if not image_name1 or not image_name2 or image_name1 == image_name2:
            raise ValidationError(
                f"pairs artifact {source} pairs[{index}] requires two different image names"
            )
        lines.append(f"{image_name1} {image_name2}\n")
    return "".join(lines)


def _materialize_pairs_artifact(task: Task, artifact: dict[str, Any]) -> Path:
    uri = artifact.get("uri")
    if not uri:
        raise ValidationError("input_artifacts.pairs.uri is required")
    source = Path(str(uri))
    if not source.is_file():
        raise ValidationError(f"input_artifacts.pairs.uri does not exist: {source}")
    artifact_format = str(artifact.get("artifact_format") or "")
    media_type = str(artifact.get("media_type") or "")
    if artifact_format == "sfmapi.pairs.image_names.v1" and (
        media_type == "application/json" or source.suffix.lower() == ".json"
    ):
        stage = Paths(get_settings()).workspace_root / "_stage" / task.task_id
        stage.mkdir(parents=True, exist_ok=True)
        pairs_path = stage / "input_artifact_pairs.txt"
        pairs_path.write_text(_pair_text_from_json_artifact(source), encoding="utf-8", newline="\n")
        return pairs_path
    return source


def _materialize_explicit_pairs(
    task: Task,
    pairs: dict[str, Any],
    input_artifacts: dict[str, Any] | None,
) -> Path:
    if pairs.get("pairs_blob_sha"):
        return get_blob_store().local_path(str(pairs["pairs_blob_sha"]))

    pair_artifact = (input_artifacts or {}).get("pairs")
    if isinstance(pair_artifact, dict):
        return _materialize_pairs_artifact(task, pair_artifact)

    image_pairs = pairs.get("image_pairs") or []
    if not isinstance(image_pairs, list) or not image_pairs:
        raise ValidationError(
            "pairs.strategy=explicit requires pairs.image_pairs, pairs.pairs_blob_sha, "
            "or input_artifacts.pairs"
        )

    stage = Paths(get_settings()).workspace_root / "_stage" / task.task_id
    stage.mkdir(parents=True, exist_ok=True)
    pairs_path = stage / "explicit_pairs.txt"
    with pairs_path.open("w", encoding="utf-8", newline="\n") as fh:
        for index, pair in enumerate(image_pairs):
            if not isinstance(pair, dict):
                raise ValidationError(f"pairs.image_pairs[{index}] must be an object")
            image_name1 = str(pair.get("image_name1") or "")
            image_name2 = str(pair.get("image_name2") or "")
            if not image_name1 or not image_name2 or image_name1 == image_name2:
                raise ValidationError(
                    f"pairs.image_pairs[{index}] requires two different image names"
                )
            fh.write(f"{image_name1} {image_name2}\n")
    return pairs_path


def _match_options(
    task: Task,
    pairs: dict[str, Any],
    matcher: dict[str, Any],
    input_artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pairs = dict(pairs)
    matcher = dict(matcher)
    pairs.pop("input_artifacts", None)
    matcher.pop("input_artifacts", None)

    if pairs.get("strategy") == "explicit":
        pairs_path = _materialize_explicit_pairs(task, pairs, input_artifacts)
        pairs["pairs_path"] = str(pairs_path)
        pairs["match_list_path"] = str(pairs_path)

    pairs_backend_options = dict(pairs.get("backend_options") or {})
    matcher_backend_options = dict(matcher.get("backend_options") or {})
    portable_pairs = {
        k: v
        for k, v in pairs.items()
        if k not in {"provider", "image_pairs", "backend_options", "input_artifacts"}
        and v is not None
    }
    portable_matcher = {
        k: v
        for k, v in matcher.items()
        if k not in {"provider", "backend_options", "input_artifacts"} and v is not None
    }
    options = {
        **portable_pairs,
        **portable_matcher,
        **pairs_backend_options,
        **matcher_backend_options,
    }
    if pairs.get("provider") is not None:
        options["pairs_provider"] = pairs["provider"]
    if matcher.get("provider") is not None:
        options["matcher_provider"] = matcher["provider"]
    options["portable"] = {"pairs": portable_pairs, "matcher": portable_matcher}
    options["backend_options"] = {
        "pairs": pairs_backend_options,
        "matcher": matcher_backend_options,
    }
    pairs["backend_options"] = pairs_backend_options
    matcher["backend_options"] = matcher_backend_options
    options["pairs"] = pairs
    options["matcher"] = matcher
    if input_artifacts:
        options["input_artifacts"] = input_artifacts
    return options


def _match_image_root(inputs: dict[str, Any]) -> Path | None:
    """The materialized image directory, when the match inputs carry one.

    Only the detector-free io matching path needs image refs; the
    detector-based path pairs FeatureSets from the io store and never
    touches images. When neither an explicit ``image_root`` nor a local
    ``materialization`` is present this returns ``None`` and a
    detector-free matcher raises an honest 501.
    """
    root = inputs.get("image_root")
    if root:
        return Path(str(root))
    materialization = inputs.get("materialization") or {}
    if isinstance(materialization, dict) and materialization.get("image_root"):
        return Path(str(materialization["image_root"]))
    return None


@task_handler("match")
def run(task: Task) -> dict[str, Any]:
    inputs, spec = read_state(task)
    db_path = Path(inputs["database_path"])
    pairs = spec.get("pairs") or {}
    matcher = spec.get("matcher") or {}
    input_artifacts = inputs.get("input_artifacts") or {}
    strategy = pairs.get("strategy", "exhaustive")
    backend = backend_for_match_stage(pairs, matcher)
    io_matcher = io_pair_matcher(backend)
    if io_matcher is not None:
        # Preferred path (P8 Step 6): the backend implements the neutral
        # sceneapi-io PairMatcher contract. Detector-based matchers pair the
        # FeatureSets the io extractor persisted (indexed correspondences);
        # detector-free matchers pair image refs (coordinate
        # correspondences). Results land in the io correspondence store the
        # verify + map stages read via the shared database_path anchor.
        progress = get_progress_reporter()
        if progress is not None:
            progress.phase_started("matching")
        out = run_io_match(
            io_matcher,
            backend=backend,
            db_path=db_path,
            pairs_spec=pairs,
            matcher_spec=matcher,
            input_artifacts=input_artifacts,
            image_root=_match_image_root(inputs),
            progress=progress,
        )
        if progress is not None:
            progress.phase_completed("matching")
        return out
    match = require_backend_method(
        backend,
        "match",
        capability=f"pairs.{strategy}",
    )
    progress = get_progress_reporter()
    if progress is not None:
        progress.phase_started("matching")
    summary = call_with_optional_progress(
        match,
        progress=progress,
        database_path=db_path,
        mode=strategy,
        options=_match_options(task, pairs, matcher, input_artifacts),
    )
    if progress is not None:
        progress.phase_completed("matching")

    backend_name = str(getattr(backend, "name", "unknown"))
    artifacts: list[dict[str, Any]] = [
        {
            "kind": f"matches.database.{backend_name}",
            "name": "match-database",
            "uri": str(db_path),
            "summary": summary if isinstance(summary, dict) else {},
            "artifact_format": f"{backend_name}.matches.database.v1",
            "schema_version": 1,
            "producer": {"backend": backend_name},
        }
    ]
    out: dict[str, Any] = {
        "database_path": str(db_path),
        "strategy": strategy,
        **summary,
        "artifacts": artifacts,
    }
    # Best-effort: dump the raw correspondence graph so the
    # reconstruction-level read endpoint has fresh data. Failure here
    # doesn't fail match — geometric verification is what matters.
    with contextlib.suppress(Exception):
        iter_correspondences = require_backend_method(
            backend,
            "iter_correspondences",
            capability="observations.by_point",
        )
        written = export_correspondence_graph(
            iter_correspondences(database_path=db_path), db_path.parent
        )
        out["correspondence_graph_path"] = str(written)
        artifacts.append(
            {
                "kind": "matches.indexed.v1",
                "name": "correspondence_graph",
                "uri": str(written),
                "media_type": "application/json",
                "artifact_format": "sfmapi.matches.indexed.v1",
                "schema_version": 1,
                "producer": {"backend": backend_name},
            }
        )
    return out
