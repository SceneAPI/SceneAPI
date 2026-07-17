param(
    [string]$PgPort = "55432",
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = "Stop"
$PgName = "sfmapi-pg-ci"
$PgUser = "sfm"
$PgPass = "sfm"
$PgDb = "sfmapi_test"

function Cleanup {
    docker rm -f $PgName 2>$null | Out-Null
}
trap { Cleanup; throw }

Write-Host "Starting postgres on port $PgPort ..." -ForegroundColor Cyan
docker run -d --rm `
    --name $PgName `
    -e "POSTGRES_USER=$PgUser" `
    -e "POSTGRES_PASSWORD=$PgPass" `
    -e "POSTGRES_DB=$PgDb" `
    -p "$($PgPort):5432" `
    postgres:16-alpine | Out-Null

Write-Host "Waiting for postgres..." -NoNewline
for ($i = 0; $i -lt 30; $i++) {
    docker exec $PgName pg_isready -U $PgUser -d $PgDb 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host " ready."
        break
    }
    Start-Sleep -Seconds 1
    Write-Host "." -NoNewline
}

$env:SCENEAPI_DB_URL = "postgresql+psycopg://$($PgUser):$($PgPass)@localhost:$($PgPort)/$PgDb"
$env:SCENEAPI_AUTH_MODE = "none"
$env:SCENEAPI_PYCOLMAP_AVAILABLE = "false"

Write-Host "=== Postgres test run ===" -ForegroundColor Cyan
& uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) { Cleanup; exit $LASTEXITCODE }
& uv run pytest -q -m "not needs_pycolmap" @ExtraArgs
$rc = $LASTEXITCODE
Cleanup
exit $rc
