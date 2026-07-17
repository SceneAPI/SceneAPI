"""Canonical-JSON + content-address helpers.

`canonical_json` returns deterministic UTF-8 bytes for any JSON-compatible
value; we use it as the input to every cache key. `content_address` returns
lowercase hex sha256 — the on-disk blob layout is `blobs/{sha[:2]}/{sha}`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, BinaryIO

CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunk


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_default,
    ).encode("utf-8")


def _default(obj: Any) -> Any:
    # Pydantic models export via .model_dump if available
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON-serializable")


def content_address(data: bytes | Iterable[bytes]) -> str:
    h = hashlib.sha256()
    if isinstance(data, (bytes, bytearray, memoryview)):
        h.update(data)
    else:
        for chunk in data:
            h.update(chunk)
    return h.hexdigest()


def stream_sha256(reader: BinaryIO, *, chunk_size: int = CHUNK_SIZE) -> tuple[str, int]:
    h = hashlib.sha256()
    total = 0
    while True:
        chunk = reader.read(chunk_size)
        if not chunk:
            break
        h.update(chunk)
        total += len(chunk)
    return h.hexdigest(), total


def hash_dict(value: dict[str, Any]) -> str:
    return content_address(canonical_json(value))
