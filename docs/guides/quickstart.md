# Quickstart

This guide brings up the full sfmapi stack on a single host and walks
through a project → upload → dataset → reconstruction loop using
the Python SDK. Allow ~10 minutes plus the time to build the SfM
backend on the GPU host (much longer the first time).

## Prerequisites

- Docker (with `docker compose`)
- A GPU host with a pycolmap-compatible CUDA stack already installed
- `uv` and Python 3.12 on PATH for SDK use

## 1. Bring up web + redis + postgres

```bash
git clone https://github.com/sfmapi/sfmapi
cd sfmapi
cp deploy/.env.example deploy/.env
# Edit deploy/.env: set SFMAPI_PG_PASS, SFMAPI_AUTH_MODE=api_key
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d
```

The web container runs migrations on start, then serves
`uvicorn` on `:8080`. Verify:

```bash
curl http://localhost:8080/healthz   # {"status":"ok"}
curl http://localhost:8080/version   # {"sfmapi":"0.0.1","pycolmap_available":false,...}
```

## 2. Install the worker on the GPU host

Either run the bootstrap script from the latest release zip:

```powershell
# Download worker-installer-vX.Y.Z.zip from GitHub releases, then:
Expand-Archive worker-installer-vX.Y.Z.zip -DestinationPath C:\sfmapi-installer
cd C:\sfmapi-installer\worker-installer
.\bootstrap-worker.ps1 `
    -DbUrl "postgresql+psycopg://sfm:secret@db.internal:5432/sfmapi" `
    -RedisUrl "redis://redis.internal:6379/0" `
    -GpuUuid 0
```

Or manually: see [Deployment](deployment.md).

## 3. Issue an API key

```bash
curl -sX POST http://localhost:8080/v1/admin/api-keys \
    -H 'Content-Type: application/json' \
    -d '{"tenant_id":"my-tenant","name":"oncall"}' | jq
```

Save the `raw_key` field; that's your bearer token.

## 4. Talk to the API from Python

```bash
pip install sfmapi-client
```

```python
from sfmapi_client import SfmApiClient, IncrementalSpec

with SfmApiClient("http://localhost:8080", api_key="sfm_xxx") as c:
    proj = c.create_project("vacation-2026")

    # Upload images one-by-one (the SDK chunks under the hood).
    shas = []
    for path in ("img1.jpg", "img2.jpg", "img3.jpg", "img4.jpg"):
        with open(path, "rb") as fh:
            shas.append(c.upload_bytes(fh.read(), content_type="image/jpeg"))

    ds = c.create_dataset(
        proj.project_id,
        name="trip",
        source={"kind": "upload",
                "entries": [{"name": p, "blob_sha": s}
                            for p, s in zip(("img1.jpg", "img2.jpg", "img3.jpg", "img4.jpg"), shas)]},
    )

    job = c.run_pipeline(
        proj.project_id,
        dataset_id=ds.dataset_id,
        image_root="/data/img",
        image_list=["img1.jpg", "img2.jpg", "img3.jpg", "img4.jpg"],
        spec=IncrementalSpec(),
    )
    print("submitted:", job.job_id)

    detail = c.get_job(job.job_id)
    print({t.kind: t.status for t in detail.tasks})
```

## 5. Watch progress

```python
for event in c.stream_events(job.job_id):
    print(event["kind"], event.get("phase"), event.get("current"))
```

`stream_events` yields parsed
[ProgressEvent](../reference/api.md#progressevent) dicts. Reconnects
resume from the last `event_id` automatically.

## 6. Read the result

```python
recon = c.get_reconstruction(detail.tasks[-1].outputs_ref["recon_id"])
seqs = c.list_snapshots(recon.recon_id)
cameras = c.read_snapshot_file(recon.recon_id, seqs[-1], "cameras.json")
points  = c.read_snapshot_file(recon.recon_id, seqs[-1], "points.bin")
```

`points.bin` is in the
[binary points format](../reference/api.md#binary-points-format) —
fixed 26 B/record, parseable with `struct` or via `sfmapi_client`'s
helper (planned).

## 7. Tear down

```bash
docker compose -f deploy/docker-compose.yml down -v
```

## Where to go next

- [Architecture](architecture.md) — why the boundaries are where they are.
- [Multi-tenancy](multitenancy.md) — auth, quotas, fair-share scheduling.
- [Deployment](deployment.md) — multi-host scale-out, observability.
- [SDK reference](../sdk/index.md) — full surface.
