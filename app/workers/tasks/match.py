"""Matching task — pair selection (PairsSpec) + per-pair matcher
(MatcherSpec).

The ``spec`` half of the task state carries ``{pairs: {...},
matcher: {...}}`` (the AIP-202 split shape). ``pairs.strategy``
selects which image pairs to consider; ``matcher.type`` selects the
per-pair algorithm."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from app.adapters.progress import call_with_optional_progress
from app.adapters.registry import get_backend
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.paths import Paths
from app.db.models import Task
from app.storage.blobs import get_blob_store
from app.storage.correspondence_emit import export_correspondence_graph
from app.workers._task_io import read_state
from app.workers.progress import get_progress_reporter
from app.workers.tasks._registry import task_handler


def _materialize_explicit_pairs(task: Task, pairs: dict[str, Any]) -> Path:
    if pairs.get("pairs_blob_sha"):
        return get_blob_store().local_path(str(pairs["pairs_blob_sha"]))

    image_pairs = pairs.get("image_pairs") or []
    if not isinstance(image_pairs, list) or not image_pairs:
        raise ValidationError(
            "pairs.strategy=explicit requires pairs.image_pairs or pairs.pairs_blob_sha"
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
        pairs_path = _materialize_explicit_pairs(task, pairs)
        pairs["pairs_path"] = str(pairs_path)
        pairs["match_list_path"] = str(pairs_path)

    pairs_backend_options = dict(pairs.get("backend_options") or {})
    matcher_backend_options = dict(matcher.get("backend_options") or {})
    matcher_options = dict(matcher.get("matcher_options") or {})
    portable_pairs = {
        k: v
        for k, v in pairs.items()
        if k not in {"provider", "image_pairs", "backend_options", "input_artifacts"}
        and v is not None
    }
    portable_matcher = {
        k: v
        for k, v in matcher.items()
        if k not in {"provider", "backend_options", "matcher_options", "input_artifacts"}
        and v is not None
    }
    options = {
        **matcher_options,
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
    options["legacy_options"] = {"matcher_options": matcher_options}
    options["matcher_options"] = matcher_options
    pairs["backend_options"] = pairs_backend_options
    matcher["backend_options"] = matcher_backend_options
    options["pairs"] = pairs
    options["matcher"] = matcher
    if input_artifacts:
        options["input_artifacts"] = input_artifacts
    return options


@task_handler("match")
def run(task: Task) -> dict[str, Any]:
    inputs, spec = read_state(task)
    db_path = Path(inputs["database_path"])
    pairs = spec.get("pairs") or {}
    matcher = spec.get("matcher") or {}
    input_artifacts = inputs.get("input_artifacts") or {}
    strategy = pairs.get("strategy", "exhaustive")
    backend = get_backend()
    progress = get_progress_reporter()
    if progress is not None:
        progress.phase_started("matching")
    summary = call_with_optional_progress(
        backend.match,
        progress=progress,
        database_path=db_path,
        mode=strategy,
        options=_match_options(task, pairs, matcher, input_artifacts),
    )
    if progress is not None:
        progress.phase_completed("matching")

    out: dict[str, Any] = {"database_path": str(db_path), "strategy": strategy, **summary}
    # Best-effort: dump the raw correspondence graph so the
    # reconstruction-level read endpoint has fresh data. Failure here
    # doesn't fail match — geometric verification is what matters.
    with contextlib.suppress(Exception):
        written = export_correspondence_graph(
            backend.iter_correspondences(database_path=db_path), db_path.parent
        )
        out["correspondence_graph_path"] = str(written)
    return out
