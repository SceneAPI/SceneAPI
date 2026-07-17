"""Cross-dialect column types.

Both SQLite and Postgres are first-class targets. We avoid Postgres-only
features (`JSONB`, `UUID`, `BIGSERIAL`) and use plain types that work on
both.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import CHAR, JSON, BigInteger, DateTime, String, TypeDecorator
from sqlalchemy.engine.interfaces import Dialect

from sceneapi.server.core.ids import ID_LEN


class ULIDType(TypeDecorator[str]):
    impl = CHAR(ID_LEN)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else str(value)


__all__ = ["CHAR", "JSON", "BigInteger", "DateTime", "String", "ULIDType"]
