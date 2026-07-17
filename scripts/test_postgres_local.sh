#!/usr/bin/env bash
# Spin up an ephemeral Postgres in Docker, run the dual-DB tests, tear it down.
# Mirrors the GH Actions `test-postgres` job for local repro.
set -euo pipefail

PG_NAME="sfmapi-pg-ci"
PG_PORT="${PG_PORT:-55432}"
PG_USER="sfm"
PG_PASS="sfm"
PG_DB="sfmapi_test"

cleanup() {
    docker rm -f "$PG_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Starting postgres on port $PG_PORT ..."
docker run -d --rm \
    --name "$PG_NAME" \
    -e POSTGRES_USER="$PG_USER" \
    -e POSTGRES_PASSWORD="$PG_PASS" \
    -e POSTGRES_DB="$PG_DB" \
    -p "${PG_PORT}:5432" \
    postgres:16-alpine >/dev/null

echo -n "Waiting for postgres"
for _ in $(seq 1 30); do
    if docker exec "$PG_NAME" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
        echo " ready."
        break
    fi
    echo -n "."
    sleep 1
done

export SCENEAPI_DB_URL="postgresql+psycopg://${PG_USER}:${PG_PASS}@localhost:${PG_PORT}/${PG_DB}"
export SCENEAPI_AUTH_MODE=none
export SCENEAPI_PYCOLMAP_AVAILABLE=false

echo "=== Postgres test run ==="
uv run alembic upgrade head
uv run pytest -q -m "not needs_pycolmap" "$@"
