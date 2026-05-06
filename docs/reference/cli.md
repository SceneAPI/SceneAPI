# CLI / scripts

sfmapi ships several operational scripts under `scripts/` and
`deploy/`. None of them are required for normal use, but each
captures a workflow that's tedious to do by hand.

## `scripts/test_dual_db.{sh,ps1}`

Runs the test suite under SQLite, then under Postgres
(via `SFMAPI_DB_URL_PG` or an ephemeral docker container).

## `scripts/test_postgres_local.{sh,ps1}`

Spins up `postgres:16-alpine` in docker on a sacrificial port, runs
migrations, runs the suite, tears down.

## `scripts/smoke.{sh,ps1}`

Brings up the full deploy stack and walks the public API end-to-end
(project → upload → dataset → image). Used by CI's `smoke` job.

## `deploy/install-worker.ps1` / `deploy/uninstall-worker.ps1`

Registers the worker as a Windows service via `nssm`. Multi-GPU =
one service per GPU with distinct names; `-GpuUuid` sets
`CUDA_VISIBLE_DEVICES`.

## `deploy/bootstrap-worker.ps1`

One-shot installer: clones / updates `sfmapi` and the configured
SfM backend repo (set via `COLMAP_MOD_REPO_URL` env var; defaults
to a public pycolmap fork), builds pycolmap, registers the worker
service. Ships in `worker-installer-vX.Y.Z.zip`.
