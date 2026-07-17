from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _fake_request(headers: dict[str, str] | None = None) -> object:
    class _R:
        def __init__(self) -> None:
            self.headers = headers or {}

    return _R()


async def test_default_tenant_returned() -> None:
    from sfmapi.server.core.tenancy import current_tenant

    t = await current_tenant(_fake_request())  # type: ignore[arg-type]
    assert t == "default"


async def test_get_current_tenant_after_dep_call() -> None:
    from sfmapi.server.core.tenancy import (
        clear_current_tenant,
        current_tenant,
        get_current_tenant,
    )

    clear_current_tenant()
    await current_tenant(_fake_request())  # type: ignore[arg-type]
    assert get_current_tenant() == "default"
