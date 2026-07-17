# Multi-tenancy

sfmapi is multi-tenant from migration 0001. v0 ships in *single-user*
mode (`SFMAPI_AUTH_MODE=none`) where every request resolves to the
`default` tenant — but every table, every workspace path, and every
service signature already carries `tenant_id`. Switching to real auth
is a config flip plus an API key rollout.

## What's in place from day 1

- `tenant_id CHAR(26) NOT NULL DEFAULT 'default'` on every domain
  table (`project`, `dataset`, `image_source`, `image`, `upload`,
  `maskset`, `mask`, `job`, `task`, `reconstruction`, `submodel`).
- Workspace paths are tenant-prefixed:
  `workspaces/{tenant_id}/projects/{pid}/...`.
- A FastAPI `current_tenant()` dependency injects the `tenant_id`
  string into every route signature.
- All service functions take `tenant_id` as the first kwarg and apply
  the filter in their queries; no route trusts a path parameter for
  tenant boundary.
- Quota service is wired with NOOP enforcement under `auth_mode=none`,
  but the call sites already exist.

## Switching on API key auth

1. Set `SFMAPI_AUTH_MODE=api_key` in the web container's env.
2. Restart the web container.
3. Issue a key:

   ```bash
   curl -sX POST http://localhost:8080/v1/admin/api-keys \
       -H 'Content-Type: application/json' \
       -d '{"tenant_id":"my-org","name":"oncall"}'
   ```

   Returns the raw key once — store it. The DB only persists
   `sha256(raw_key)`.

4. Tenant-scoped API requests now require `Authorization: Bearer
   sfm_xxx`. The `current_tenant()` dependency resolves the bearer to a
   tenant and injects it.

`/v1/admin/api-keys` is intentionally an operator surface, not a
tenant-scoped API. It is not protected by sfmapi's tenant API-key
dependency in either auth mode and **must be fronted by an admin-only
auth layer in production** (for example a deploy-time master key,
private control-plane network, mTLS, or an ingress policy).

## Quotas

```{eval-rst}
.. automodule:: sfmapi.server.services.quota_service
   :members:
   :no-index:
```

Two quota hooks exist when `auth_mode=api_key`:

- **storage upload gate**: upload session creation checks the tenant's
  configured storage budget against the requested upload size.
- **gpu_seconds_per_day**: rolling 24-hour sum of `gpu_seconds`
  recorded against tenant Tasks.

Quotas live on the `tenant_quota` table; either column NULL means
"no limit." Hits return `429 quota_exceeded` (see
[errors](../reference/errors.md)).

Shared S3 cache bytes and sealed snapshot bytes are not yet separately
attributed in this quota gate; operators should treat the storage quota
as upload-focused until that accounting is wired.

## Cross-tenant task scheduling

v0 has no fair-share interleaving: the queue (`ArqQueue` or
`InlineQueue`) drains tasks in enqueue order regardless of tenant, and
the locked "one GPU per instance" constraint means a single worker
processes one task at a time. Cross-tenant fairness becomes meaningful
only on a shared multi-worker pool — a deferred concern, not a v0 one.

## Tenant isolation tests

Every CRUD test in `tests/e2e/` runs through the same dep, so isolation
is enforced uniformly. Cross-tenant access (e.g., reading another
tenant's project) returns 404, not 403, to avoid leaking existence.

## Migration notes for existing data

If you started in single-user mode and have data under
`tenant_id='default'`, switching to multi-tenant means:

1. Pick a target tenant for the existing data.
2. Issue an API key bound to that tenant.
3. (Optional) Migrate data to a new `tenant_id` with a manual UPDATE
   per table — there's no built-in helper because mass-rewriting
   tenant_id is a deliberate operation, not a routine one.
