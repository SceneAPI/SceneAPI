"""Dual dispatch: prefer the sceneapi-io procedure contracts over v0.

Worker tasks prefer a registered backend that implements the neutral
sceneapi-io Protocols (``Mapper`` / ``FeatureExtractor`` /
``PairMatcher`` / ``GeometricVerifier``) and fall back to the v0
Path-based stage protocols (``run_mapping`` / ``extract_features`` /
``match`` / ``verify_matches``) otherwise. The v0 fallback is unchanged
and its sunset is a deferred decision.

The io Protocols are structural (``runtime_checkable`` checks method
presence only) and BOTH ``Mapper`` and ``PairMatcher`` declare a
``traits()`` method, so a bare ``isinstance`` check could misroute a
backend whose ``traits()`` returns the *other* family's traits type.
The resolvers here therefore verify the traits TYPE as well: a backend
only dispatches as a Mapper when ``traits()`` returns ``MapperTraits``,
and only as a PairMatcher when it returns ``MatcherTraits``.
"""

from __future__ import annotations

from sceneapi_io.mapping import Mapper, MapperTraits
from sceneapi_io.matching import (
    FeatureExtractor,
    GeometricVerifier,
    MatcherTraits,
    PairMatcher,
)

from sceneapi.server.core.logging import get_logger

_log = get_logger("sceneapi.workers.io_dispatch")


def _traits_of(backend: object) -> object | None:
    try:
        return backend.traits()  # type: ignore[attr-defined]
    except Exception as exc:  # a broken traits() must not break dispatch
        _log.debug("io_dispatch.traits_probe_failed", backend=repr(backend), error=str(exc))
        return None


def io_mapper(backend: object) -> Mapper | None:
    """The backend as a sceneapi-io Mapper, or None (v0 fallback)."""
    if not isinstance(backend, Mapper):
        return None
    if not isinstance(_traits_of(backend), MapperTraits):
        return None
    return backend


def io_feature_extractor(backend: object) -> FeatureExtractor | None:
    """The backend as a sceneapi-io FeatureExtractor, or None."""
    if not isinstance(backend, FeatureExtractor):
        return None
    return backend


def io_pair_matcher(backend: object) -> PairMatcher | None:
    """The backend as a sceneapi-io PairMatcher, or None (v0 fallback)."""
    if not isinstance(backend, PairMatcher):
        return None
    if not isinstance(_traits_of(backend), MatcherTraits):
        return None
    return backend


def io_geometric_verifier(backend: object) -> GeometricVerifier | None:
    """The backend as a sceneapi-io GeometricVerifier, or None."""
    if not isinstance(backend, GeometricVerifier):
        return None
    return backend


__all__ = [
    "io_feature_extractor",
    "io_geometric_verifier",
    "io_mapper",
    "io_pair_matcher",
]
