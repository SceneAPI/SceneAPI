"""Smoke tests that the schema creates cleanly under the configured engine.

The same test runs under both SQLite (default) and Postgres in CI by setting
SFMAPI_DB_URL — see scripts/test_dual_db.{sh,ps1}.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from sfmapi.server.db.session import get_engine

pytestmark = pytest.mark.integration


async def test_tables_exist_with_tenant_id(db_setup) -> None:
    engine = get_engine()

    def _inspect(sync_conn) -> dict[str, dict]:
        insp = inspect(sync_conn)
        out: dict[str, dict] = {}
        out["__tables__"] = {"names": insp.get_table_names()}
        for t in insp.get_table_names():
            out[t] = {c["name"]: c for c in insp.get_columns(t)}
        return out

    async with engine.connect() as conn:
        info = await conn.run_sync(_inspect)

    expected = {
        "tenant",
        "project",
        "blob",
        "image_source",
        "dataset",
        "image",
        "upload",
        "runtime_version",
        "stage_artifact",
    }
    have = set(info["__tables__"]["names"])
    assert expected.issubset(have), have

    for table in {"project", "image_source", "dataset", "image", "upload", "stage_artifact"}:
        cols = info[table]
        assert "tenant_id" in cols, f"{table} missing tenant_id"
        assert cols["tenant_id"]["nullable"] is False, f"{table}.tenant_id must be NOT NULL"
