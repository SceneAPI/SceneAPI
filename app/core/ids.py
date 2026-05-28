"""ULID factory.

ULIDs are 26-char Crockford-base32 strings, lexicographically time-sortable,
case-insensitive, unique to ~80 bits of entropy per millisecond. We use them
everywhere instead of integer auto-increment to (a) avoid Postgres-only
sequences, (b) give clients an opaque, sortable, prefix-stable id, and (c)
make multi-tenant collision avoidance trivial.
"""

from __future__ import annotations

from ulid import ULID

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
