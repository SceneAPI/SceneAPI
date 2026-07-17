param(
    [switch]$Keep,
    [int]$WebPort = 8080,
    [int]$PgPort = 55432,
    [int]$RedisPort = 56379
)

$ErrorActionPreference = "Stop"

$ProjectName = "sfmapi-smoke"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ComposeFile = Join-Path $RepoRoot "deploy\docker-compose.yml"
$BaseUrl = "http://127.0.0.1:$WebPort"

function Require-Cmd($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Required command not on PATH: $name"
    }
}

function Cleanup {
    if ($Keep) {
        Write-Host ""
        Write-Host "[-Keep] leaving stack up. Tear down with:" -ForegroundColor Yellow
        Write-Host "  docker compose -p $ProjectName -f $ComposeFile down -v"
        return
    }
    Write-Host ""
    Write-Host "Tearing down..." -ForegroundColor Cyan
    & docker compose -p $ProjectName -f $ComposeFile down -v --remove-orphans 2>$null | Out-Null
}

trap {
    Write-Host ""
    Write-Host "===== smoke FAILED — web logs =====" -ForegroundColor Red
    & docker compose -p $ProjectName -f $ComposeFile logs --tail=80 web 2>$null
    Cleanup
    throw
}

Require-Cmd docker
Require-Cmd curl.exe
Require-Cmd python

Write-Host "==> bringing up stack on web=$WebPort pg=$PgPort redis=$RedisPort" -ForegroundColor Cyan
$env:SCENEAPI_WEB_PORT = "$WebPort"
$env:SCENEAPI_PG_PORT = "$PgPort"
$env:SCENEAPI_REDIS_PORT = "$RedisPort"
$env:SCENEAPI_AUTH_MODE = "none"
$env:SCENEAPI_PG_USER = "sfm"
$env:SCENEAPI_PG_PASS = "sfm"
$env:SCENEAPI_PG_DB = "sfmapi"
& docker compose -p $ProjectName -f $ComposeFile up -d --build --wait
if ($LASTEXITCODE -ne 0) { throw "compose up failed" }

Write-Host "==> waiting for /healthz" -ForegroundColor Cyan
$ok = $false
for ($i = 1; $i -le 60; $i++) {
    try {
        Invoke-RestMethod -Method Get -Uri "$BaseUrl/healthz" -TimeoutSec 2 | Out-Null
        Write-Host "    healthz ok after ${i}s"
        $ok = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $ok) { throw "healthz never became 200" }

Write-Host "==> /version" -ForegroundColor Cyan
$ver = Invoke-RestMethod -Method Get -Uri "$BaseUrl/version"
$ver | ConvertTo-Json
if (-not $ver.sfmapi) { throw "no sfmapi version in /version" }

Write-Host "==> /metrics surface check" -ForegroundColor Cyan
$metrics = Invoke-WebRequest -Method Get -Uri "$BaseUrl/metrics"
if ($metrics.Content -notmatch "sfmapi_queue_depth") { throw "metrics surface missing" }
Write-Host "    metrics ok"

Write-Host "==> create project" -ForegroundColor Cyan
$proj = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/projects" `
    -ContentType "application/json" -Body '{"name":"smoke-proj"}'
$pid_ = $proj.project_id
Write-Host "    project_id=$pid_"

Write-Host "==> chunked upload" -ForegroundColor Cyan
$tmp = [System.IO.Path]::GetTempFileName()
$bytes = [byte[]](@(0xFF, 0xD8, 0xFF, 0xE0) + (1..2048 | ForEach-Object { Get-Random -Maximum 256 }))
[System.IO.File]::WriteAllBytes($tmp, $bytes)
$size = (Get-Item $tmp).Length
$sha = (Get-FileHash -Algorithm SHA256 $tmp).Hash.ToLower()

$initBody = @{ expected_size = [int]$size; expected_sha = $sha } | ConvertTo-Json -Compress
$init = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/uploads" `
    -ContentType "application/json" `
    -Headers @{ "Idempotency-Key" = "smoke-1" } `
    -Body $initBody
$uid = $init.upload_id
Write-Host "    upload_id=$uid size=$size"

$last = $size - 1
& curl.exe -fsS -X PATCH "$BaseUrl/v1/uploads/$uid" `
    --data-binary "@$tmp" `
    -H "Content-Range: bytes 0-${last}/${size}" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "PATCH chunk failed" }

$fin = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/uploads/$uid/finalize" `
    -ContentType "application/json" -Body "{}"
$blobSha = $fin.blob_sha
if ($blobSha -ne $sha) { throw "sha mismatch: $blobSha vs $sha" }
Remove-Item $tmp -Force

Write-Host "==> create dataset" -ForegroundColor Cyan
$dsBody = @{
    name = "ds-smoke"
    source = @{ kind = "upload"; entries = @(@{ name = "a.jpg"; blob_sha = $blobSha }) }
} | ConvertTo-Json -Depth 4 -Compress
$ds = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/projects/$pid_/datasets" `
    -ContentType "application/json" -Body $dsBody
$did = $ds.dataset_id
Write-Host "    dataset_id=$did"

Write-Host "==> register image" -ForegroundColor Cyan
$imgBody = @{ name = "a.jpg"; blob_sha = $blobSha } | ConvertTo-Json -Compress
$img = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/datasets/$did/images" `
    -ContentType "application/json" -Body $imgBody
Write-Host "    image_id=$($img.image_id)"

Write-Host "==> list images" -ForegroundColor Cyan
$list = Invoke-RestMethod -Method Get -Uri "$BaseUrl/v1/datasets/$did/images"
if ($list.items.Count -lt 1) { throw "image listing returned $($list.items.Count)" }
Write-Host "    listing ok ($($list.items.Count) images)"

Write-Host "==> idempotent re-upload returns same upload_id" -ForegroundColor Cyan
$init2 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/uploads" `
    -ContentType "application/json" `
    -Headers @{ "Idempotency-Key" = "smoke-1" } `
    -Body (@{ expected_size = [int]$size } | ConvertTo-Json -Compress)
if ($init2.upload_id -ne $uid) { throw "idempotency-key drift: $($init2.upload_id) vs $uid" }
Write-Host "    idempotency ok"

Write-Host ""
Write-Host "==== SMOKE PASSED ====" -ForegroundColor Green
Write-Host "    web=$BaseUrl  pid=$pid_  did=$did  blob=$($blobSha.Substring(0,12))..."
Cleanup
