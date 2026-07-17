# RLS on Postgres Tenancy — Design Proposal

**Status**: Conditional. Approval gated on the user's
`Postgres-only vs SQLite-compat` decision.

**Owner**: TBD.

**Companion**: `CLAUDE.md` "Locked Constraints" §2 (multi-tenant
from day 1), `app/core/tenancy.py` (current `current_tenant` dep),
`docs/guides/multitenancy.md` (current contract).

---

## Why this is conditional

This proposal **cannot be approved on its own merits** — it depends
on a strictly upstream decision the user has not yet made:

> **Stay dual-dialect** (SQLite + Postgres ANSI-SQL only)
> **OR**
> **Commit Postgres-only** (drop SQLite from production; SQLite for
> fast unit tests only).

CLAUDE.md currently locks dual-dialect (no `JSONB`, no
`SKIP LOCKED`, no `RETURNING` reliance, no Postgres-only triggers).
RLS is fundamentally a Postgres feature — SQLite has no equivalent.
**Approving this proposal is approving the upstream commit.**

If the user picks dual-dialect, this entire proposal is rejected;
the current hand-rolled `WHERE tenant_id = current_tenant()` pattern
is what remains, with all its costs documented below.

If the user picks Postgres-only, this proposal becomes the
implementation plan.

## The current pattern

Every repository / service / handler enforces tenant isolation by
hand:

```python
result = await session.execute(
    select(Dataset).where(
        Dataset.tenant_id == tenant_id,        # MANUAL, every query
        Dataset.dataset_id == dataset_id,
    )
)
```

Audit (counted across `app/services/`, `app/orchestrator/`,
`app/api/v1/`):

| File | `tenant_id` references |
|---|---|
| `services/sfm_stage_service.py` | 71 |
| `api/v1/images.py` | 33 |
| `services/dataset_service.py` | 16 |
| `api/v1/datasets.py` | 15 |
| `services/image_service.py` | 12 |
| `services/job_service.py` | 11 |
| `services/project_service.py` | 11 |
| `services/reconstruction_service.py` | 10 |
| `api/v1/localize.py` | 10 |
| ... | (~150 more across remaining files) |

Every one of those is a place a future contributor could forget
to add the `WHERE tenant_id =` clause and silently leak
cross-tenant data. Today's `tests/conformance/test_tenancy.py`
catches some — not all.

## What RLS replaces

PostgreSQL Row-Level Security policies enforce the
`tenant_id = current_setting('app.current_tenant_id')` rule **at
the database** rather than in every query. The application sets
the tenant once per session/transaction and the database refuses
to return rows from any other tenant — even on a query that
doesn't mention `tenant_id` at all.

Wire layout:

```sql
-- One-time, on every table that has tenant_id (~13 tables today).
ALTER TABLE project ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON project
  USING (tenant_id = current_setting('app.current_tenant_id', true));

-- Per request:
SET LOCAL app.current_tenant_id = '01HZTENANT0000000000000000';
-- Now every SELECT / UPDATE / DELETE on `project` is auto-filtered.
```

## Three-layer defense (recommended even if you adopt RLS)

RLS is a backstop, not a replacement, for the application-layer
filter. The recommended posture is **defense in depth**:

| Layer | Mechanism | What it catches |
|---|---|---|
| 1. Application | `WHERE tenant_id = current_tenant()` | Most common case; catches obvious mistakes during code review |
| 2. Database (RLS) | `current_setting('app.current_tenant_id')` policy | Application bug that forgets the WHERE clause |
| 3. Audit | `tests/conformance/test_tenancy.py` + a new "no-RLS-bypass" CI check | Privilege regression in `bypassrls` role grants |

The application-layer filter stays for performance (Postgres can
still use a multi-column index even with RLS on; bare RLS without
the explicit WHERE is slower because the planner can't pick the
right index). The change is **what RLS gives you on top**, not a
replacement.

## What this proposal explicitly does NOT do

- **Does not** drop SQLite support for unit tests. Tests that
  don't need RLS run on SQLite as today.
- **Does not** remove the application-layer
  `WHERE tenant_id = current_tenant()` filter. Belt + suspenders.
- **Does not** change the wire surface. SDKs, contract tests,
  consumer-visible behavior all stay identical.
- **Does not** introduce per-tenant database roles. One DB role
  for the app, RLS policies discriminate on a session GUC.

## Migration plan (if approved)

### Phase a — Plumbing (~4h)
1. Add an Alembic migration that's a no-op on SQLite (skipped
   via `op.get_bind().dialect.name`) and on Postgres:
   - `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` for every
     table with a `tenant_id` column.
   - `CREATE POLICY tenant_isolation USING (...)` per table.
2. Extend `app/db/session.py::get_db` to:
   - Read `current_tenant()` from the FastAPI dep.
   - On each request, `SET LOCAL app.current_tenant_id =
     '<value>'` at the start of the transaction.
   - The `LOCAL` qualifier scopes the GUC to the current
     transaction so connection-pool reuse doesn't leak.
3. Test under both engines: existing SQLite suite stays green
   (no-op migration), new Postgres suite asserts cross-tenant
   reads return zero rows even with explicit `WHERE` removed.

### Phase b — Conformance test (~2h)
- New `tests/conformance/test_rls_isolation.py` (Postgres-marker
  only) that:
  - Inserts rows for two tenants directly via SQL.
  - Connects as the application role with one tenant set.
  - Issues a query with no `WHERE tenant_id` and confirms it
    returns only the current tenant's rows.
- Belt-and-suspenders: a `pg_security_label` audit query asserting
  no role has been granted `BYPASSRLS`.

### Phase c — Optional later: simplify application queries (~12h)
Once Phase a + b are proven in production, the application-layer
`WHERE tenant_id = ...` clauses become redundant for correctness
(though still recommended for performance). A cleanup pass could
remove them where the planner proves it doesn't lose an index
hit. **Do not** do this in the same PR as Phase a — bundle the
migration risk separately.

## Cost summary

| Phase | LOC delta | New files | Tests | Time |
|---|---|---|---|---|
| a — Migration + session GUC | ~80 | 0 (alembic auto-named) | both engines | ~4h |
| b — Conformance + bypass audit | ~120 | 1 | Postgres-marker | ~2h |
| c — Application cleanup (optional) | -200 to -500 | 0 | regression-only | ~12h |

**Phase a + b only: ~6 hours.** Phase c is not on the critical
path and should be done piecemeal when files are touched for
unrelated reasons.

## What this fixes

- Eliminates the entire class of "forgot the `WHERE tenant_id`"
  bug.
- Lets the auth team add roles + policies without touching every
  service file.
- Makes the multi-tenant story auditable in the database
  (single source of truth via `pg_policy`).
- Removes one of the implicit constraints behind the
  "ANSI-SQL only" rule, opening the door for `JSONB` / `SKIP
  LOCKED` / `RETURNING` adoption (separate proposals).

## What this costs

- **Lock-in to Postgres for production.** Backup/restore tooling,
  ops runbooks, deployment templates — all change. Existing
  SQLite-as-prod deployments must migrate before this lands.
- **One more concept** for new contributors to learn (RLS
  policies + the GUC convention). Mitigated by clear docs and
  the `tests/conformance/test_rls_isolation.py` example.
- **Slight session-pool complexity.** `SET LOCAL` per
  transaction is fine but requires the connection pool be
  configured for transaction-scoped reuse, not session-scoped
  (otherwise the GUC would persist across requests). Already
  the case for asyncpg with `async_sessionmaker(expire_on_commit=False)`,
  but worth a CLAUDE.md note.

## Decision tree

```
Is the user willing to drop SQLite from production deployments?
├─ YES → Approve this proposal. Phase a + b first, c later.
│        Update CLAUDE.md to remove the dual-dialect lock.
│        Document the migration in Phase 5 plan.
└─ NO  → Reject this proposal. Document the rejection in
         CLAUDE.md so future contributors don't reopen the
         conversation. Accept the ongoing cost of hand-rolled
         tenant filters + the `tests/conformance/test_tenancy.py`
         coverage as the only line of defense.
```

There is no middle path. RLS without Postgres doesn't exist.

## Recommendation

The dual-dialect rule was the right call for v0 — SQLite-as-prod
is a real deployment scenario for single-user / on-prem / embedded
sfmapi instances. But every multi-tenant production use case
(hosted SaaS, multi-customer) is on Postgres anyway. The natural
inflection point is when sfmapi gains its first non-default tenant
in production. **Until then, dual-dialect remains correct.**

When that inflection point arrives — typically when auth ships
real `api_key` provisioning per tenant — flip to Postgres-only
and run Phase a + b in the same release. That release is the
right place to take the migration cost; Phase c is per-file
cleanup over the following months.

For now: **leave dual-dialect locked.** Ship this proposal as a
"ready when you are" reference document. When auth lands and
multi-tenant SaaS becomes a real deployment target, this design
becomes the implementation plan.
