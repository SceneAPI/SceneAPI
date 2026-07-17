"""Worker-side option envelope helpers."""

from __future__ import annotations

from typing import Any

_STRUCTURED_OPTION_KEYS = {
    "backend_options",
    "portable",
    "input_artifacts",
}


def stage_options(
    spec: dict[str, Any],
    *,
    skip_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Return a backend options envelope for a single stage.

    The returned dict contains:

    - ``portable`` with sfmapi-owned fields,
    - ``backend_options`` with provider-specific fields,
    - a flat merged view for simple backend implementations.
    """
    skip = set(skip_keys or set())
    backend_options = dict(spec.get("backend_options") or {})
    portable = {
        key: value
        for key, value in spec.items()
        if key not in _STRUCTURED_OPTION_KEYS and key not in skip and value is not None
    }
    options: dict[str, Any] = {}
    options.update(portable)
    options.update(backend_options)
    options["portable"] = portable
    options["backend_options"] = backend_options
    return options


__all__ = ["stage_options"]
