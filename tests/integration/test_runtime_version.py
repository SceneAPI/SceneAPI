from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import RuntimeVersion
from app.services.runtime_version_service import ensure_runtime_version

pytestmark = pytest.mark.integration


async def test_ensure_runtime_version_idempotent(session) -> None:
    s = get_settings()
    a = await ensure_runtime_version(session, s)
    b = await ensure_runtime_version(session, s)
    assert a.rv_id == b.rv_id
    rows = (await session.execute(select(RuntimeVersion))).scalars().all()
    assert len(rows) == 1


async def test_ensure_runtime_version_distinct_on_change(session, monkeypatch) -> None:
    s = get_settings()
    a = await ensure_runtime_version(session, s)
    monkeypatch.setattr(s, "runtime_version_id", "deadbeef")
    b = await ensure_runtime_version(session, s)
    assert a.rv_id != b.rv_id
