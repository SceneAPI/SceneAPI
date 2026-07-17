from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


async def test_issue_then_use_api_key(client, monkeypatch) -> None:
    issue = await client.post("/v1/admin/api-keys", json={"tenant_id": "tenant-acme", "name": "ci"})
    assert issue.status_code == 201, issue.text
    raw = issue.json()["raw_key"]
    api_key_id = issue.json()["api_key_id"]

    # Switch into api_key mode for subsequent requests.
    from sceneapi.server.core.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_mode", "api_key")

    # Without auth header: should 403.
    no_auth = await client.post("/v1/projects", json={"name": "p"})
    assert no_auth.status_code == 403

    # With valid header: tenant resolved, project created under that tenant.
    ok = await client.post(
        "/v1/projects",
        json={"name": "p"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["tenant_id"] == "tenant-acme"

    # Revoke key, then any subsequent request fails.
    rev = await client.delete(f"/v1/admin/api-keys/{api_key_id}")
    assert rev.status_code == 200
    assert rev.json()["revoked"] is True

    after = await client.post(
        "/v1/projects",
        json={"name": "p2"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert after.status_code == 403


async def test_admin_lists_keys(client) -> None:
    await client.post("/v1/admin/api-keys", json={"tenant_id": "t1"})
    await client.post("/v1/admin/api-keys", json={"tenant_id": "t2", "name": "robot"})
    resp = await client.get("/v1/admin/api-keys")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 2
