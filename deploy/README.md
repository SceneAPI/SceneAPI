# sfmapi deployment

For development or single-host use see the
[Quickstart](https://sfmapi.github.io/guides/quickstart.html) — no
Docker, no Redis, no Postgres needed. This guide covers
production-shape multi-instance deploys.

Two pieces:

1. **Web tier** — stateless FastAPI behind your load balancer.
2. **Worker tier** — one service per GPU, each running a backend
   package you ship separately. Workers consume tasks from Redis,
   read/write Postgres, and stream sealed snapshots to disk or
   shared storage.

Web and worker tiers are deliberately decoupled — the web tier
scales horizontally and a single Postgres + Redis can serve any
number of GPU worker hosts.

## 1. Bring up web + redis + postgres

```bash
cp deploy/.env.example deploy/.env
# edit deploy/.env: set SCENEAPI_PG_PASS, SCENEAPI_AUTH_MODE, etc.
# Multi-instance flips: SCENEAPI_QUEUE_BACKEND=arq, SCENEAPI_DB_URL=postgresql://...
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d
```

The web container runs `alembic upgrade head` on start, then serves
`uvicorn sceneapi.server.main:app` on `:8080`. `/healthz`, `/readyz`, `/version`,
`/metrics` are exposed.

Issue an API key (in `api_key` mode):

```bash
curl -sX POST http://localhost:8080/v1/admin/api-keys \
    -H 'Content-Type: application/json' \
    -d '{"tenant_id":"my-tenant","name":"oncall"}'
```

## 2. Install a worker on a GPU host (Windows)

Workers need:

- The same CUDA / driver stack the backend package was built against.
- A backend package that satisfies `sceneapi.backends.SfmBackend`
  installed in the worker venv (e.g. an editable checkout of your
  pycolmap fork plus a thin `register_backend()` wrapper).
- `sfmapi` itself, also installed editable.
- `nssm` on `PATH` (https://nssm.cc/).

Build the worker venv:

```powershell
uv venv
# install your backend package — replace with your own URL
uv pip install -e <path-to-your-backend-package>
uv pip install -e ".[dev]"
$env:SCENEAPI_BACKEND = "<your-backend-name>"
```

Install (Administrator):

```powershell
.\deploy\install-worker.ps1 `
    -ServiceName sfmapi-worker `
    -DbUrl "postgresql+psycopg://sfm:secret@db.internal:5432/sfmapi" `
    -RedisUrl "redis://redis.internal:6379/0" `
    -GpuUuid "0"
```

Multi-GPU host? One service per GPU, distinct service names:

```powershell
.\deploy\install-worker.ps1 -ServiceName sfmapi-worker-0 -GpuUuid "0"
.\deploy\install-worker.ps1 -ServiceName sfmapi-worker-1 -GpuUuid "1"
```

Each service writes `logs\<ServiceName>.std{out,err}.log` next to the
repo. Tail with:

```powershell
Get-Content -Wait .\logs\sfmapi-worker.stdout.log
```

Uninstall:

```powershell
.\deploy\uninstall-worker.ps1 -ServiceName sfmapi-worker
```

## Multi-host scale-out

- Run `docker compose` once on a control plane host (or replace
  postgres + redis with managed services).
- Install the worker service on each GPU host with your chosen
  backend package and pointed at the central `SCENEAPI_DB_URL` +
  `SCENEAPI_REDIS_URL`.
- The fair-share scheduler interleaves work across tenants;
  per-host concurrency-1 is enforced by the supervisor + ARQ
  defaults.

## Helm

A reference chart lives under `deploy/helm/sfmapi/` for Kubernetes
deploys. The web tier is its own deployment; workers run as a
DaemonSet that lands on GPU-capable nodes (replace the worker image
with one that bundles your chosen backend). Postgres and Redis are
pulled in as Bitnami subcharts; both can be disabled if you front
the install with managed services.

## Smoke test the deploy

Once Docker is running:

```bash
bash scripts/smoke.sh                 # bring up, run flow, tear down
bash scripts/smoke.sh --keep          # leave stack up on success
SCENEAPI_WEB_PORT=18080 bash scripts/smoke.sh
```

Or on Windows:

```powershell
.\scripts\smoke.ps1
.\scripts\smoke.ps1 -Keep -WebPort 18080
```

Steps the script verifies:
healthz → version → metrics surface → create project → chunked upload
(init / PATCH / finalize) → create dataset (upload source) → register
image → list images → idempotency-key replay returns same upload_id.

On failure, the script prints the last 80 lines of the `web` container
logs before tearing the stack down (unless `--keep` / `-Keep`).

## Troubleshooting

- **Worker won't start**: `arq` missing from venv → re-run
  `uv pip install -e .`. Or `SCENEAPI_BACKEND` set to a name not in
  the registry → check the backend package registers itself on
  import.
- **`/healthz` 503 from web**: check container logs; usually the
  postgres dependency is still starting (compose `condition: healthy`
  should prevent this).
- **No tasks running**: confirm the worker can reach Redis
  (`redis-cli -h <host> ping` from the worker host) and that
  `SCENEAPI_QUEUE_BACKEND=arq` (the standalone-default `inline`
  queue runs in-process and ignores Redis).
- **501 CapabilityUnavailableError everywhere**: no backend
  registered. The stub backend ships with sfmapi for tests; in
  production you need a real backend package on the worker host.
