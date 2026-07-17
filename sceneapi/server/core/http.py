"""HTTP-layer helpers shared across routes.

Lives here (not in `sceneapi.server.api`) so service-side code can also produce
ETag values for cache lookups without depending on FastAPI types.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import Response


def weak_etag(*parts: Any) -> str:
    """Build a weak ETag from arbitrary stringifiable parts.

    "Weak" because we hash a synthetic representation rather than the
    bytes themselves; it changes whenever any input part changes.
    """
    h = hashlib.sha256()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
        h.update(b"\x00")
    return f'W/"{h.hexdigest()[:32]}"'


def file_etag(path: Path) -> str:
    """Strong ETag based on file path + size + mtime.

    Sealed-snapshot files are immutable once written, so this is
    effectively a strong ETag for them.
    """
    st = path.stat()
    return f'"{path.name}:{st.st_size}:{int(st.st_mtime_ns)}"'


def if_none_match_hit(request: Request, etag: str) -> bool:
    inm = request.headers.get("if-none-match")
    if not inm:
        return False
    # Per RFC 7232: comma-separated list, weak validators OK.
    candidates = [c.strip() for c in inm.split(",")]
    return etag in candidates or "*" in candidates


def not_modified(etag: str) -> Response:
    return Response(status_code=304, headers={"ETag": etag})
