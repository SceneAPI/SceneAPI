from __future__ import annotations

from datetime import UTC

import pytest

from sfmapi.server.core.errors import TenantViolationError
from sfmapi.server.services import api_key_service

pytestmark = pytest.mark.integration


async def test_issue_and_resolve_round_trip(session) -> None:
    raw, _row = await api_key_service.issue_key(session, tenant_id="t-A", name="ci")
    await session.commit()
    assert raw.startswith("sfm_")
    tid = await api_key_service.resolve_tenant(session, key=raw)
    assert tid == "t-A"


async def test_revoked_key_rejected(session) -> None:
    from datetime import datetime

    raw, row = await api_key_service.issue_key(session, tenant_id="t-B")
    row.revoked_at = datetime.now(UTC)
    await session.commit()
    with pytest.raises(TenantViolationError):
        await api_key_service.resolve_tenant(session, key=raw)
