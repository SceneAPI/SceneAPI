# Authentication

sfmapi has two authentication modes selected by the
`SCENEAPI_AUTH_MODE` environment variable.

## `auth_mode=none` (default — dev / single-tenant)

Every request resolves to the `default` tenant. No `Authorization`
header is required; admin routes are open. Use this for local
development, single-user deployments, and the ephemeral mode demo
(`SCENEAPI_EPHEMERAL=true`).

Operationally this is the equivalent of "trust whatever's on the
other side of the socket" — terminate at a reverse proxy or
front-end auth layer if you need a perimeter.

## `auth_mode=api_key` (multi-tenant)

Every tenant-scoped non-public request must carry a bearer API key.

```http
Authorization: Bearer sfm_<26-char-ULID>_<random>
```

Public routes (no key required): `GET /healthz`, `GET /readyz`,
`GET /version`, `GET /spec`, `GET /metrics`.

Keys are scoped to a single `tenant_id` and resolved on every
request through `sceneapi.server.core.tenancy.current_tenant`. Cross-tenant
reads and writes use the same `problem+json` error shape as other
API errors.

### Issuing keys

The `/v1/admin/api-keys` endpoints mint and revoke tenant API keys, but
they are not tenant-scoped and are not protected by sfmapi's tenant
API-key dependency. Production deployments must put these routes behind
an admin-only control-plane layer such as a private network, mTLS,
ingress policy, or deploy-time master key.

```bash
# Mint a new key (returns the raw key ONCE - store it).
curl -X POST http://localhost:8000/v1/admin/api-keys \
     -H 'Content-Type: application/json' \
     -d '{"tenant_id": "acme", "name": "ci-bot"}'

# List keys (ApiKeyOut rows: api_key_id, tenant_id, name, revoked).
curl http://localhost:8000/v1/admin/api-keys

# Revoke (returns the row with revoked=true; key stops working).
curl -X DELETE http://localhost:8000/v1/admin/api-keys/$KEY_ID
```

`raw_key` is the only time you see the secret. The DB stores a
content-addressed digest; lose the raw key, mint a new one.

## Tenant boundaries

The web layer never trusts a caller-provided `tenant_id` — it pulls
the tenant from `current_tenant()` and adds it to every query in
the service layer. A row that exists under a different tenant looks
identical to "not present" (404).

For implementation details see [multi-tenancy](../guides/multitenancy.md).

## SDK usage

All three SDKs accept an `api_key` parameter (or env var):

```python
# Python
from scenesdk import Client
client = Client(base_url="https://api.example.com", token="sfm_...")
```

```ts
// TypeScript after building/installing the repository package
import { createSfmApiClient } from "@scenesdk/client/generated";
const client = createSfmApiClient({
  baseUrl: "https://api.example.com",
  apiKey: process.env.SCENEAPI_KEY,
});
```

```cpp
// C++
sfmapi::Client client({"https://api.example.com", api_key, transport});
```
