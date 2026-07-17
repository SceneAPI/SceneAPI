# Quickstart

Stand up sceneapi on a single host with no Docker, no Redis, no
Postgres. Allow about five minutes. The defaults give you SQLite
on disk, filesystem blob storage, and an in-process worker — perfect
for development, embedded use, and trying the wire surface.

To run real reconstructions you will additionally
[register a backend](backend_implementations.md) — that step is
optional and orthogonal to everything below.

## Prerequisites

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/) (or `pip` if you prefer)
- A few hundred MB of disk for the Python wheel cache

## 1. Install

```bash
git clone https://github.com/sfmapi/sfmapi
cd sfmapi
uv venv
uv pip install -e ".[dev]"
cp .env.example .env
uv run alembic upgrade head
```

`alembic upgrade head` creates `sfmapi.db` (SQLite) in the working
directory using the schema. The `.env` carries the standalone
defaults (queue=inline, blobs=fs, sqlite db).

The base API reads image dimensions with header-only parsing. Install
the `image-processing` extra only for optional thumbnail rendering and
`dhash` similarity:

```bash
uv pip install -e ".[image-processing]"
```

## 2. Start the server

```bash
uv run uvicorn sceneapi.runtime:create_app --factory --reload
```

Verify:

```bash
curl http://localhost:8080/healthz       # {"status":"ok"}
curl http://localhost:8080/version       # {"sfmapi":"0.1.0","backend":null}
curl http://localhost:8080/v1/spec       # spec metadata + openapi.json link
```

`backend` is `null` because no SfM engine is registered. The wire
surface is fully responsive; SfM-specific operations will return
`501 CapabilityUnavailableError` until you register a backend.

## 3. Drive the API end-to-end

```bash
# Create a project
curl -sX POST http://localhost:8080/v1/projects \
    -H 'Content-Type: application/json' \
    -d '{"name": "vacation-2026"}' | tee /tmp/proj.json
PID=$(jq -r .project_id /tmp/proj.json)

# Upload an image (chunked, content-addressed)
UID=$(curl -sX POST http://localhost:8080/v1/uploads \
    -H 'Content-Type: application/json' \
    -d '{"expected_size": '$(stat -c%s img1.jpg)'}' | jq -r .upload_id)
curl -sX PATCH http://localhost:8080/v1/uploads/$UID \
    -H "Content-Range: bytes 0-$(($(stat -c%s img1.jpg)-1))/$(stat -c%s img1.jpg)" \
    --data-binary @img1.jpg
SHA=$(curl -sX POST http://localhost:8080/v1/uploads/$UID:finalize \
    -H 'Content-Type: application/json' -d '{}' | jq -r .blob_sha)

# Create a dataset that references the uploaded blob
curl -sX POST http://localhost:8080/v1/projects/$PID/datasets \
    -H 'Content-Type: application/json' \
    -d '{"name": "trip", "source": {"kind": "upload",
         "entries": [{"name": "img1.jpg", "blob_sha": "'$SHA'"}]}}' | jq .
```

## 4. Use a typed SDK (Python or TypeScript)

```bash
uv pip install ../sfmapi-sdk/python/sceneapi_client_gen
```

```python
from sceneapi_client_gen import Client
from sceneapi_client_gen.api.projects import projects_create
from sceneapi_client_gen.models import ProjectCreate

with Client(base_url="http://localhost:8080") as c:
    project = projects_create.sync(client=c, body=ProjectCreate(name="vacation-2026"))
    print(project.project_id)
```

For TypeScript:

```bash
cd ../sfmapi-sdk/typescript && npm install && npm run build
```

```typescript
import { createSfmApiClient } from "@sceneapi/client/generated";

const c = createSfmApiClient({ baseUrl: "http://localhost:8080" });
const { data: project } = await c.raw.POST("/v1/projects", {
  body: { name: "vacation-2026" },
});
```

## Where to go next

- [Implement a backend](backend_implementations.md) — make the SfM
  endpoints actually do reconstructions.
- [Architecture](architecture.md) — why the boundaries are where
  they are.
- [Curl tour](../reference/curl_tour.md) — the same flow as above
  with every shape laid out.
- [API reference](../reference/api.md) — full endpoint list.
- [Deployment](deployment.md) — multi-host scale-out, Postgres,
  Redis-backed ARQ workers, observability.
