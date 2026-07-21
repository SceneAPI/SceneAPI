"""The sceneio matching bridge (P8 Step 6).

Writers + readers between the neutral sceneio sparse-correspondence
types (``FeatureSet`` / ``PairCorrespondences`` / ``TwoViewGeometry``)
and the sealed on-disk artifact forms the worker pipeline threads
between stages. This is the engine-artifact bridge the Step-5
scaffolding stubbed out; it lets the io ``FeatureExtractor`` /
``PairMatcher`` / ``GeometricVerifier`` Protocols drive the
extract / match / verify tasks and feed a ``CorrespondenceGraph`` back
into a ``requires_correspondences=True`` io ``Mapper``.

Store layout
------------
The v0 pipeline threads its work between stages through one COLMAP
``database.db`` living under the reconstruction root; every stage
receives the same ``database_path`` and mutates it in place. The io
bridge mirrors that exactly with a numpy-native store beside the (never
created) database, anchored on the SAME ``database_path`` so every
stage — and the map task's ``load_correspondence_graph`` — agrees on
its location without any artifact threading::

    <database_path.parent>/io_correspondence/
        features/<stem>.npz     one FeatureSet per image
        matches/<stem>.npz      one PairCorrespondences per matched pair
        verified/<stem>.npz     the geometrically-verified subset (+ geometry)

Each ``.npz`` carries the true image name(s) inside it, so the
filesystem-safe stems never have to be reversible and image names with
path separators round-trip losslessly. Descriptors keep their exact
dtype (``np.savez`` preserves it); ``load_correspondence_graph`` reads
the store back into a ``CorrespondenceGraph`` equal to the input within
dtype.

The stage artifacts these writers emit reuse the v0 envelope shape
(``kind`` / ``name`` / ``uri`` / ``summary`` / ``schema_version`` /
``producer``) with an io-native ``artifact_format`` id, so a downstream
v0 stage consumes an io stage's output indistinguishably at the
task-fixture level (``database_path`` threads; the artifact list is
well-formed) — the ``uri`` points at the io store rather than a COLMAP
database, which the classical mapper reads only through this bridge.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
from sceneio.data import (
    CorrespondenceGraph,
    FeatureSet,
    PairCorrespondences,
    TwoViewGeometry,
)
from sceneio.imagesource import MaterializedImage
from sceneio.matching import (
    FeatureExtractor,
    GeometricVerifier,
    MatchingOptions,
    PairMatcher,
)

from sceneapi.server.core.errors import CapabilityUnavailableError
from sceneapi.server.core.logging import get_logger

_log = get_logger("sceneapi.workers.io_match")

_STORE_DIRNAME = "io_correspondence"
_FEATURES = "features"
_MATCHES = "matches"
_VERIFIED = "verified"


# ---- store location + filesystem-safe stems -------------------------------


def correspondence_store_root(db_path: Path | str) -> Path:
    """The io correspondence store anchored on the stage ``database_path``.

    Every stage receives the same ``database_path`` (the COLMAP database
    the v0 pipeline threads), so anchoring the io store on its parent
    gives all stages — extract, match, verify, and the map task's
    ``load_correspondence_graph`` — one agreed location.
    """
    return Path(db_path).parent / _STORE_DIRNAME


def _safe_stem(key: str) -> str:
    """A unique, filesystem-safe stem for ``key`` (image name or pair id).

    The true name is stored inside the ``.npz``; this only has to be
    unique and portable, so a readable prefix plus a short content hash
    both avoids collisions and keeps ``a/b.jpg`` vs ``a_b.jpg`` distinct.
    """
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).hexdigest()
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:64].strip("._-") or "img"
    return f"{cleaned}-{digest}"


def _pair_key(image_a: str, image_b: str) -> str:
    return f"{image_a}\x00{image_b}"


# ---- FeatureSet <-> npz ----------------------------------------------------


def write_feature_set(store_root: Path, image_name: str, feature_set: FeatureSet) -> Path:
    """Persist one image's ``FeatureSet`` into the store; return its path.

    The same image name always maps to the same file, so a re-run of
    extract overwrites in place (as the v0 database would).
    """
    features_dir = Path(store_root) / _FEATURES
    features_dir.mkdir(parents=True, exist_ok=True)
    path = features_dir / f"{_safe_stem(image_name)}.npz"
    arrays: dict[str, np.ndarray] = {
        "name": np.array(str(image_name)),
        "keypoints": np.asarray(feature_set.keypoints, dtype=np.float32),
    }
    if feature_set.descriptors is not None:
        arrays["descriptors"] = np.asarray(feature_set.descriptors)  # dtype preserved
    if feature_set.scores is not None:
        arrays["scores"] = np.asarray(feature_set.scores, dtype=np.float32)
    np.savez(path, **arrays)
    return path


def _feature_set_from_npz(data: Any) -> tuple[str, FeatureSet]:
    keys = set(data.files)
    name = str(data["name"])
    descriptors = np.asarray(data["descriptors"]) if "descriptors" in keys else None
    scores = np.asarray(data["scores"], dtype=np.float32) if "scores" in keys else None
    feature_set = FeatureSet(
        keypoints=np.asarray(data["keypoints"], dtype=np.float32),
        descriptors=descriptors,
        scores=scores,
    )
    return name, feature_set


def load_feature_sets(store_root: Path | None) -> dict[str, FeatureSet]:
    """Every persisted ``FeatureSet`` keyed by image name (may be empty)."""
    features: dict[str, FeatureSet] = {}
    if store_root is None:
        return features
    features_dir = Path(store_root) / _FEATURES
    if not features_dir.is_dir():
        return features
    for path in sorted(features_dir.glob("*.npz")):
        with np.load(path, allow_pickle=False) as data:
            name, feature_set = _feature_set_from_npz(data)
        features[name] = feature_set
    return features


# ---- PairCorrespondences (+ TwoViewGeometry) <-> npz ----------------------


def write_pair_correspondences(
    store_root: Path,
    image_a: str,
    image_b: str,
    pair: PairCorrespondences,
    *,
    verified: bool = False,
) -> Path:
    """Persist one pair's correspondences; return its path.

    ``verified=True`` writes into the ``verified/`` subdir (the geometric
    verifier's filtered subset, which may carry ``TwoViewGeometry``);
    otherwise into ``matches/`` (the raw matcher output). The two live
    side by side exactly as the v0 pipeline keeps ``correspondence_graph``
    and ``two_view_geometries`` distinct.
    """
    out_dir = Path(store_root) / (_VERIFIED if verified else _MATCHES)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_safe_stem(_pair_key(image_a, image_b))}.npz"
    arrays: dict[str, np.ndarray] = {
        "image_a": np.array(str(image_a)),
        "image_b": np.array(str(image_b)),
        "mode": np.array(str(pair.mode)),
    }
    if pair.mode == "indexed":
        arrays["indices"] = np.asarray(pair.indices)  # integer dtype preserved
    else:
        arrays["coordinates_a"] = np.asarray(pair.coordinates_a, dtype=np.float32)
        arrays["coordinates_b"] = np.asarray(pair.coordinates_b, dtype=np.float32)
    if pair.scores is not None:
        arrays["scores"] = np.asarray(pair.scores, dtype=np.float32)
    geometry = pair.geometry
    if geometry is not None:
        arrays["has_geometry"] = np.array(True)
        for field_name in ("E", "F", "H"):
            value = getattr(geometry, field_name)
            if value is not None:
                arrays[f"geom_{field_name}"] = np.asarray(value, dtype=np.float64)
        if geometry.num_inliers is not None:
            arrays["geom_num_inliers"] = np.array(int(geometry.num_inliers))
    np.savez(path, **arrays)
    return path


def _geometry_from_npz(data: Any, keys: set[str]) -> TwoViewGeometry | None:
    if "has_geometry" not in keys:
        return None
    return TwoViewGeometry(
        E=np.asarray(data["geom_E"], dtype=np.float64) if "geom_E" in keys else None,
        F=np.asarray(data["geom_F"], dtype=np.float64) if "geom_F" in keys else None,
        H=np.asarray(data["geom_H"], dtype=np.float64) if "geom_H" in keys else None,
        num_inliers=int(data["geom_num_inliers"]) if "geom_num_inliers" in keys else None,
    )


def _pair_from_npz(data: Any) -> tuple[str, str, PairCorrespondences]:
    keys = set(data.files)
    image_a = str(data["image_a"])
    image_b = str(data["image_b"])
    mode = str(data["mode"])
    scores = np.asarray(data["scores"], dtype=np.float32) if "scores" in keys else None
    geometry = _geometry_from_npz(data, keys)
    if mode == "indexed":
        pair = PairCorrespondences.from_indices(
            np.asarray(data["indices"]), scores=scores, geometry=geometry
        )
    else:
        pair = PairCorrespondences.from_coordinates(
            np.asarray(data["coordinates_a"], dtype=np.float32),
            np.asarray(data["coordinates_b"], dtype=np.float32),
            scores=scores,
            geometry=geometry,
        )
    return image_a, image_b, pair


def load_pair_correspondences(
    store_root: Path | None,
) -> dict[tuple[str, str], PairCorrespondences]:
    """Every persisted pair keyed by ``(image_a, image_b)``.

    The verified subset (``verified/``) overrides the raw pair
    (``matches/``) for the same key — a verified pass supersedes the
    matcher output, just as the v0 database's two-view geometries
    supersede its raw matches.
    """
    pairs: dict[tuple[str, str], PairCorrespondences] = {}
    if store_root is None:
        return pairs
    for subdir in (_MATCHES, _VERIFIED):  # verified wins (processed last)
        pair_dir = Path(store_root) / subdir
        if not pair_dir.is_dir():
            continue
        for path in sorted(pair_dir.glob("*.npz")):
            with np.load(path, allow_pickle=False) as data:
                image_a, image_b, pair = _pair_from_npz(data)
            pairs[(image_a, image_b)] = pair
    return pairs


def load_correspondence_graph(store_root: Path | None) -> CorrespondenceGraph | None:
    """Read sealed feature/match artifacts back into a ``CorrespondenceGraph``.

    Returns ``None`` when the store is absent or empty — the honest
    signal a ``requires_correspondences=True`` mapper turns into a 501.
    A populated store yields the graph the classical mapper consumes
    (verified pairs preferred over raw). Replaces the return-``None``
    Step-5 stub that lived in ``_io_map``.
    """
    if store_root is None:
        return None
    store_root = Path(store_root)
    features = load_feature_sets(store_root)
    pairs = load_pair_correspondences(store_root)
    if not features and not pairs:
        return None
    return CorrespondenceGraph(features=features, pairs=pairs)


# ---- stage artifact envelopes (v0-shaped) ---------------------------------


def _artifact(
    *,
    kind: str,
    name: str,
    uri: Path,
    summary: Mapping[str, Any],
    artifact_format: str,
    backend_name: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "uri": str(uri),
        "summary": dict(summary),
        "artifact_format": artifact_format,
        "schema_version": 1,
        "producer": {"backend": backend_name},
    }


# ---- options bridge --------------------------------------------------------


def matching_options_from_spec(spec: Mapping[str, Any]) -> MatchingOptions:
    """A ``MatchingOptions`` carrying the stage spec as ``extra``.

    Native conformers read implementation-specific knobs (model id,
    feature type, ...) out of ``extra``; the seed is lifted to the
    typed field when present.
    """
    raw_seed = spec.get("seed")
    seed = raw_seed if isinstance(raw_seed, int) and not isinstance(raw_seed, bool) else None
    extra = {key: value for key, value in dict(spec).items() if key != "seed"}
    return MatchingOptions(seed=seed, extra=extra)


# ---- pair enumeration (consistent with how v0 enumerates work) ------------


def enumerate_pairs(
    strategy: str,
    pairs_spec: Mapping[str, Any],
    image_names: list[str],
    input_artifacts: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """Ordered, distinct ``(image_a, image_b)`` pairs for the strategy.

    Mirrors the v0 pair-selection vocabulary: ``exhaustive`` (all
    combinations), ``sequential`` (a sliding window of ``overlap``),
    and ``explicit`` (an image-pair list carried in the spec or a
    ``pairs`` input artifact). Keys are ordered ``(a, b)`` so the
    correspondence columns follow the same side order.
    """
    normalized = str(strategy).replace("-", "_").lower()
    ordered = sorted(image_names)
    if normalized == "exhaustive":
        return list(combinations(ordered, 2))
    if normalized == "sequential":
        overlap = pairs_spec.get("overlap") or pairs_spec.get("window_size") or 1
        overlap = int(overlap) if isinstance(overlap, int) and overlap >= 1 else 1
        pairs: list[tuple[str, str]] = []
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 : index + 1 + overlap]:
                pairs.append((first, second))
        return pairs
    if normalized == "explicit":
        return _explicit_pairs(pairs_spec, input_artifacts)
    # Unknown strategies degrade to exhaustive rather than failing the
    # whole stage — the same neutral default the v0 match task uses.
    return list(combinations(ordered, 2))


def _explicit_pairs(
    pairs_spec: Mapping[str, Any],
    input_artifacts: Mapping[str, Any] | None,
) -> list[tuple[str, str]]:
    raw: list[Any] = []
    candidate = pairs_spec.get("image_pairs")
    if isinstance(candidate, list):
        raw = candidate
    elif input_artifacts:
        artifact = input_artifacts.get("pairs")
        if isinstance(artifact, dict) and isinstance(artifact.get("pairs"), list):
            raw = artifact["pairs"]
    out: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            first = item.get("image_name1") or item.get("image1") or item.get("name1")
            second = item.get("image_name2") or item.get("image2") or item.get("name2")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            first, second = item[0], item[1]
        else:
            continue
        first, second = str(first or ""), str(second or "")
        if first and second and first != second:
            out.append((first, second))
    return out


# ---- per-image / per-pair orchestration (called by the task handlers) -----


def run_io_extract(
    extractor: FeatureExtractor,
    *,
    backend: object,
    db_path: Path,
    image_root: Path,
    image_list: list[str],
    spec: Mapping[str, Any],
    progress: Any | None = None,
) -> dict[str, Any]:
    """Run the io ``FeatureExtractor`` per image; persist FeatureSets.

    Returns the same ``{database_path, num_images, artifacts}`` shape the
    v0 extract task returns, so downstream stages thread on
    ``database_path`` unchanged.
    """
    store_root = correspondence_store_root(db_path)
    options = matching_options_from_spec(spec)
    total = len(image_list)
    num_keypoints = 0
    for index, name in enumerate(image_list):
        if progress is not None:
            progress.phase_progress("feature_extraction", current=index, total=total)
        image_ref = MaterializedImage(name=name, abs_path=Path(image_root) / name)
        feature_set = extractor.extract(image_ref, options=options)
        write_feature_set(store_root, name, feature_set)
        num_keypoints += len(feature_set)
    if progress is not None:
        progress.phase_progress("feature_extraction", current=total, total=total)
    backend_name = str(getattr(backend, "name", "unknown"))
    summary = {"num_images": total, "num_keypoints": num_keypoints, "engine": "sceneio"}
    _log.debug("io_match.extract", backend=backend_name, num_images=total)
    return {
        "database_path": str(db_path),
        "num_images": total,
        "num_keypoints": num_keypoints,
        "artifacts": [
            _artifact(
                kind=f"features.database.{backend_name}",
                name="feature-database",
                uri=store_root / _FEATURES,
                summary=summary,
                artifact_format=f"{backend_name}.features.io.v1",
                backend_name=backend_name,
            )
        ],
    }


def run_io_match(
    matcher: PairMatcher,
    *,
    backend: object,
    db_path: Path,
    pairs_spec: Mapping[str, Any],
    matcher_spec: Mapping[str, Any],
    input_artifacts: Mapping[str, Any] | None = None,
    image_root: Path | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Run the io ``PairMatcher`` per pair; persist PairCorrespondences.

    Detector-based matchers (``traits.detector_free=False``) match the
    persistent ``FeatureSet`` operands loaded from the io feature store
    and emit ``"indexed"`` correspondences; detector-free matchers match
    image refs and emit ``"coordinates"``. The set of images to pair is
    the feature store for the detector-based path (the images extract
    produced) and requires an ``image_root`` for the detector-free path.
    """
    store_root = correspondence_store_root(db_path)
    traits = matcher.traits()
    options = matching_options_from_spec(matcher_spec)
    strategy = str(pairs_spec.get("strategy", "exhaustive"))
    backend_name = str(getattr(backend, "name", "unknown"))

    if traits.detector_free:
        if image_root is None:
            raise CapabilityUnavailableError(
                capability=f"pairs.{strategy}",
                reason=(
                    "the registered sceneio PairMatcher is detector-free "
                    "(traits.detector_free=True) and needs image refs, but the "
                    "match task carried no image_root to build them from"
                ),
            )
        image_names = _detector_free_image_names(pairs_spec, input_artifacts, image_root)
        pairs = enumerate_pairs(strategy, pairs_spec, image_names, input_artifacts)
        num_matches = 0
        for image_a, image_b in pairs:
            ref_a = MaterializedImage(name=image_a, abs_path=Path(image_root) / image_a)
            ref_b = MaterializedImage(name=image_b, abs_path=Path(image_root) / image_b)
            pair = matcher.match_pair(ref_a, ref_b, options=options)
            write_pair_correspondences(store_root, image_a, image_b, pair)
            num_matches += len(pair)
    else:
        features = load_feature_sets(store_root)
        if not features:
            raise CapabilityUnavailableError(
                capability=f"pairs.{strategy}",
                reason=(
                    "the registered sceneio PairMatcher is detector-based "
                    "(traits.detector_free=False) but no sealed FeatureSets were "
                    "found in the io store — run the io feature extractor first"
                ),
            )
        pairs = enumerate_pairs(strategy, pairs_spec, sorted(features), input_artifacts)
        num_matches = 0
        for image_a, image_b in pairs:
            if image_a not in features or image_b not in features:
                continue
            pair = matcher.match_pair(features[image_a], features[image_b], options=options)
            write_pair_correspondences(store_root, image_a, image_b, pair)
            num_matches += len(pair)

    num_pairs = len(pairs)
    summary = {
        "strategy": strategy,
        "num_matched_pairs": num_pairs,
        "num_matches": num_matches,
        "detector_free": bool(traits.detector_free),
        "engine": "sceneio",
    }
    _log.debug("io_match.match", backend=backend_name, num_pairs=num_pairs)
    return {
        "database_path": str(db_path),
        "strategy": strategy,
        "num_matched_pairs": num_pairs,
        "num_matches": num_matches,
        "artifacts": [
            _artifact(
                kind=f"matches.database.{backend_name}",
                name="match-database",
                uri=store_root / _MATCHES,
                summary=summary,
                artifact_format=f"{backend_name}.matches.io.v1",
                backend_name=backend_name,
            )
        ],
    }


def run_io_verify(
    verifier: GeometricVerifier,
    *,
    backend: object,
    db_path: Path,
    spec: Mapping[str, Any],
    progress: Any | None = None,
) -> dict[str, Any]:
    """Run the io ``GeometricVerifier`` per pair; persist verified subsets.

    Reads the raw pairs the io matcher persisted, filters each through
    ``verify``, and writes the geometrically-consistent subset (which may
    carry ``TwoViewGeometry``) into the ``verified/`` store.
    """
    store_root = correspondence_store_root(db_path)
    options = matching_options_from_spec(spec)
    backend_name = str(getattr(backend, "name", "unknown"))
    matches_dir = store_root / _MATCHES

    num_verified_pairs = 0
    num_inliers = 0
    if matches_dir.is_dir():
        for path in sorted(matches_dir.glob("*.npz")):
            with np.load(path, allow_pickle=False) as data:
                image_a, image_b, pair = _pair_from_npz(data)
            verified = verifier.verify(pair, options=options)
            write_pair_correspondences(store_root, image_a, image_b, verified, verified=True)
            if len(verified):
                num_verified_pairs += 1
                num_inliers += len(verified)

    summary = {
        "num_verified_pairs": num_verified_pairs,
        "num_inliers": num_inliers,
        "engine": "sceneio",
    }
    _log.debug("io_match.verify", backend=backend_name, num_verified_pairs=num_verified_pairs)
    return {
        "database_path": str(db_path),
        "num_verified_pairs": num_verified_pairs,
        "num_inliers": num_inliers,
        "artifacts": [
            _artifact(
                kind=f"matches.database.verified.{backend_name}",
                name="verified-match-database",
                uri=store_root / _VERIFIED,
                summary=summary,
                artifact_format=f"{backend_name}.matches.io.verified.v1",
                backend_name=backend_name,
            )
        ],
    }


def _detector_free_image_names(
    pairs_spec: Mapping[str, Any],
    input_artifacts: Mapping[str, Any] | None,
    image_root: Path,
) -> list[str]:
    """Image names for a detector-free match run.

    Prefers an explicit image list from the spec; falls back to the
    names referenced by an explicit pair list; else enumerates the
    materialized image directory.
    """
    listed = pairs_spec.get("image_list") or pairs_spec.get("image_names")
    if isinstance(listed, list) and listed:
        return [str(name) for name in listed]
    explicit = _explicit_pairs(pairs_spec, input_artifacts)
    if explicit:
        names: list[str] = []
        for first, second in explicit:
            names.extend((first, second))
        return sorted(set(names))
    root = Path(image_root)
    if root.is_dir():
        return sorted(
            entry.name
            for entry in root.iterdir()
            if entry.is_file() and not entry.name.startswith(".")
        )
    return []


__all__ = [
    "correspondence_store_root",
    "enumerate_pairs",
    "load_correspondence_graph",
    "load_feature_sets",
    "load_pair_correspondences",
    "matching_options_from_spec",
    "run_io_extract",
    "run_io_match",
    "run_io_verify",
    "write_feature_set",
    "write_pair_correspondences",
]
