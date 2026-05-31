"""Public sanitization of task ``outputs_ref`` -- strip host filesystem paths.

The worker writes raw result payloads to ``outputs_ref_json``, often carrying
host filesystem paths (a sealed snapshot dir, artifact uris, workspace mounts).
Those must not leak to API clients. This sanitizer is applied when a task is
serialized for the wire, and mirrors the C++ port's ``sanitize_public_json``
BYTE-FOR-BYTE so the served job shape is identical across tiers:

* ``host_path`` / ``workspace`` keys are dropped;
* a plugin's ``url`` is dropped when ``plugin_id`` + ``provider`` siblings exist;
* a local-path ``uri`` -> ``null``; a local-path ``path`` key is dropped and its
  basename lifted to ``name`` (if absent);
* any other ``*path*`` / ``*file*`` key with a local-path value -> its basename;
* non-local uris (``http://``, ``memory://``, ``s3://`` ...) pass through.
"""

from __future__ import annotations

from typing import Any


def _has_url_scheme(s: str) -> bool:
    i = s.find("://")
    if i <= 0:  # not found, or "://" at position 0 -> no scheme
        return False
    return all(c.isalnum() or c in "+-." for c in s[:i])


def _is_local_uri(s: str) -> bool:
    if not s:
        return False
    if s.startswith("file://"):
        return True
    if s[0] in ("/", "\\"):
        return True
    if len(s) >= 3 and s[0].isalpha() and s[1] == ":" and s[2] in ("\\", "/"):
        return True  # drive-letter path, e.g. C:\ or C:/
    return not _has_url_scheme(s)


def _base_name(p: str) -> str:
    i = max(p.rfind("/"), p.rfind("\\"))
    return p if i == -1 else p[i + 1:]


def sanitize_public_outputs(value: Any) -> Any:
    if isinstance(value, list):
        return [sanitize_public_outputs(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, child in value.items():
        if key in ("host_path", "workspace"):
            continue
        if key == "url" and "plugin_id" in value and "provider" in value:
            continue
        if key in ("path", "uri") and isinstance(child, str) and _is_local_uri(child):
            if key == "path":
                if not isinstance(value.get("name"), str):
                    out["name"] = _base_name(child)
                continue
            out[key] = None
            continue
        if (
            isinstance(child, str)
            and _is_local_uri(child)
            and ("path" in key or "file" in key)
        ):
            out[key] = _base_name(child)
            continue
        out[key] = sanitize_public_outputs(child)
    return out


__all__ = ["sanitize_public_outputs"]
