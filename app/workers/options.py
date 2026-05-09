"""Worker-side option envelope helpers.

Workers pass both the modern structured shape and legacy flat aliases
to backends. That keeps older backend packages working while giving
new backends a clear split between portable sfmapi fields and
provider-specific ``backend_options``.
"""

from __future__ import annotations

from typing import Any

_STRUCTURED_OPTION_KEYS = {
    "backend_options",
    "extractor_options",
    "matcher_options",
    "legacy_options",
    "portable",
}


def stage_options(
    spec: dict[str, Any],
    *,
    legacy_option_fields: tuple[str, ...] = (),
    skip_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Return a backend options envelope for a single stage.

    The returned dict contains:

    - flat portable fields for old backends,
    - flat backend-specific keys for old backends,
    - ``portable`` with sfmapi-owned fields,
    - ``backend_options`` with provider-specific fields,
    - ``legacy_options`` with deprecated alias bags.
    """
    skip = set(skip_keys or set())
    backend_options = dict(spec.get("backend_options") or {})
    legacy_options: dict[str, Any] = {}
    for field in legacy_option_fields:
        value = spec.get(field)
        if isinstance(value, dict):
            legacy_options[field] = dict(value)

    portable = {
        key: value
        for key, value in spec.items()
        if key not in _STRUCTURED_OPTION_KEYS and key not in skip and value is not None
    }
    options: dict[str, Any] = {}
    for value in legacy_options.values():
        if isinstance(value, dict):
            options.update(value)
    options.update(portable)
    options.update(backend_options)
    options["portable"] = portable
    options["backend_options"] = backend_options
    options["legacy_options"] = legacy_options
    for field, value in legacy_options.items():
        options[field] = value
    return options


__all__ = ["stage_options"]
