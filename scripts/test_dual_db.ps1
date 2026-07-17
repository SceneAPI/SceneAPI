param([string[]]$ExtraArgs = @())

$ErrorActionPreference = "Stop"

Write-Host "=== SQLite ===" -ForegroundColor Cyan
$env:SCENEAPI_DB_URL = "sqlite+aiosqlite:///./test_sqlite.db"
& uv run pytest -q -m "not needs_pycolmap and not needs_postgres" @ExtraArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($env:SCENEAPI_DB_URL_PG) {
    Write-Host "=== Postgres ===" -ForegroundColor Cyan
    $env:SCENEAPI_DB_URL = $env:SCENEAPI_DB_URL_PG
    & uv run pytest -q -m "not needs_pycolmap" @ExtraArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "(skipping Postgres run; set SCENEAPI_DB_URL_PG to enable)"
}
