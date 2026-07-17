"""Provider-aware backend resolution for worker tasks."""

from __future__ import annotations

from typing import Any

from sfm_hub.routing import ensure_provider_enabled
from sfmapi.server.adapters.registry import get_backend
from sfmapi.server.core.errors import ValidationError


def _get_backend_for_provider(provider: str | None) -> Any:
    try:
        if provider is not None:
            ensure_provider_enabled(provider)
        return get_backend(provider=provider)
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc


def backend_for_stage(spec: dict[str, Any] | None) -> Any:
    """Resolve the backend for a stage spec.

    If sfm_hub validation resolved ``spec.provider``, execution uses that
    provider. Otherwise this falls back to the process-wide ``SFMAPI_BACKEND``
    selector for legacy and single-backend deployments.
    """

    provider = None
    if isinstance(spec, dict) and spec.get("provider") is not None:
        provider = str(spec["provider"])
    return _get_backend_for_provider(provider)


def backend_for_match_stage(pairs: dict[str, Any], matcher: dict[str, Any]) -> Any:
    """Resolve a backend for the combined pair-selection/matching task."""

    pairs_provider = pairs.get("provider")
    matcher_provider = matcher.get("provider")
    if (
        pairs_provider is not None
        and matcher_provider is not None
        and str(pairs_provider) != str(matcher_provider)
    ):
        raise ValidationError(
            "pairs.provider and matcher.provider resolve to different providers, but the "
            "current match worker executes pair selection and matching as one backend call. "
            "Use the same provider for both, or pass a precomputed pairs artifact to a "
            "separate matcher stage."
        )
    provider = matcher_provider if matcher_provider is not None else pairs_provider
    return _get_backend_for_provider(str(provider) if provider is not None else None)


__all__ = ["backend_for_match_stage", "backend_for_stage"]
