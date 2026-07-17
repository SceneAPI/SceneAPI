#!/usr/bin/env bash
# Run the test suite twice: once on SQLite, once on Postgres.
# CI must pass both. The Postgres run requires SCENEAPI_DB_URL_PG set.
set -euo pipefail

echo "=== SQLite ==="
SCENEAPI_DB_URL="sqlite+aiosqlite:///./test_sqlite.db" \
    uv run pytest -q -m "not needs_pycolmap and not needs_postgres" "$@"

if [[ -n "${SCENEAPI_DB_URL_PG:-}" ]]; then
    echo
    echo "=== Postgres (existing instance) ==="
    SCENEAPI_DB_URL="${SCENEAPI_DB_URL_PG}" \
        uv run pytest -q -m "not needs_pycolmap" "$@"
elif command -v docker >/dev/null; then
    echo
    echo "=== Postgres (ephemeral docker) ==="
    bash scripts/test_postgres_local.sh "$@"
else
    echo
    echo "(skipping Postgres run; set SCENEAPI_DB_URL_PG or install docker)"
fi
