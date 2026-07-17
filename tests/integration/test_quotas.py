from __future__ import annotations

import pytest

from sfmapi.server.core.errors import QuotaExceededError
from sfmapi.server.services import quota_service

pytestmark = pytest.mark.integration


async def test_storage_quota_enforced_under_api_key_mode(session, monkeypatch) -> None:
    from sfmapi.server.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "auth_mode", "api_key")
    q = await quota_service.get_or_create_quota(session, tenant_id="t-Q")
    q.storage_bytes_max = 1024
    q.storage_bytes_used = 1000
    await session.commit()
    with pytest.raises(QuotaExceededError):
        await quota_service.check_storage(session, tenant_id="t-Q", additional=200)


async def test_storage_quota_skipped_when_auth_none(session) -> None:
    # default auth_mode == "none" -> NOOP regardless of values
    await quota_service.check_storage(session, tenant_id="t-X", additional=10**18)
