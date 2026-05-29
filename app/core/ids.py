"""Id helpers used across sfmapi.

The codebase juggles two distinct id families that are easy to confuse:

1. **Resource ids** — ULIDs identifying instances of mutable resources
   (project_id, dataset_id, job_id, recon_id, ...). 26-char
   Crockford-base32, lexicographically time-sortable, ~80 bits of
   entropy per millisecond. Helpers: ``new_id`` / ``is_id``.
2. **Contract ids** — namespaced strings identifying entries in the
   *catalog* the backend advertises (config_id, action_id, contract_id,
   provider_id). Constrained by regex, not by length. Helpers:
   ``is_namespaced_id`` / ``is_provider_id``.

See ``docs/reference/job_configuration.md`` for the contract-id table
(which dotted segment means what in which field).
"""

from __future__ import annotations

import re

from ulid import ULID

# ---- resource ids (ULID-shaped) ----

ID_LEN = 26


def new_id() -> str:
    return str(ULID())


def is_id(value: str) -> bool:
    if not isinstance(value, str) or len(value) != ID_LEN:
        return False
    try:
        ULID.from_str(value)
    except (ValueError, TypeError):
        return False
    return True


# ---- contract ids (namespaced / provider) ----
#
# These are the regexes downstream callers (sfm_hub.models,
# app.adapters.backend_*) import instead of re-declaring the same literals.

# config_id / action_id / artifact contract_id — namespaced dotted,
# at least one `.`-separated segment after the first.
NAMESPACED_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)+$"
NAMESPACED_ID_RE = re.compile(NAMESPACED_ID_PATTERN)

# provider_id — looser; dots are allowed but not required.
PROVIDER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
PROVIDER_ID_RE = re.compile(PROVIDER_ID_PATTERN)

# artifact key (artifact kind / format id) — provider-id shape with a
# 96-char cap. Re-exported by app.core.artifacts (the artifact vocabulary
# module) so callers keep using artifacts.ARTIFACT_KEY_RE; the pattern
# lives here so every id class has one home.
ARTIFACT_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$"
ARTIFACT_KEY_RE = re.compile(ARTIFACT_KEY_PATTERN)


def is_namespaced_id(value: str) -> bool:
    """Return whether ``value`` looks like a config_id / action_id /
    contract_id (namespaced dotted)."""
    return bool(NAMESPACED_ID_RE.match(value))


def is_provider_id(value: str) -> bool:
    """Return whether ``value`` looks like a provider_id."""
    return bool(PROVIDER_ID_RE.match(value))


def is_artifact_key(value: str) -> bool:
    """Return whether ``value`` is a valid artifact key (kind / format id)."""
    return bool(ARTIFACT_KEY_RE.fullmatch(value))
