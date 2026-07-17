<#
.SYNOPSIS
    Install/update the sfmapi worker as a Windows service via nssm.

.DESCRIPTION
    The worker is NOT containerized on Windows: pycolmap (from
    ../colmap_mod) is built against the host CUDA + cuDSS stack and
    cannot live in a non-NVIDIA-runtime container easily. Instead, we
    register the worker as an nssm service that runs in a venv, with
    `SCENEAPI_DB_URL` and `SCENEAPI_REDIS_URL` pointing at the docker-compose
    stack on the same machine (or a remote host).

.PARAMETER ServiceName
    Service name (default: sfmapi-worker).

.PARAMETER VenvPath
    Path to the venv that has pycolmap + sfmapi installed
    (default: .\.venv).

.PARAMETER WorkingDir
    Repo root the worker should run from. Defaults to script's parent.

.PARAMETER DbUrl
    `SCENEAPI_DB_URL` env var (default: postgres on localhost:5432).

.PARAMETER RedisUrl
    `SCENEAPI_REDIS_URL` env var (default: redis on localhost:6379).

.PARAMETER GpuUuid
    Optional CUDA_VISIBLE_DEVICES value (e.g. "0").

.EXAMPLE
    .\install-worker.ps1 -GpuUuid "0"

.EXAMPLE
    .\install-worker.ps1 -ServiceName sfmapi-worker-1 `
        -DbUrl "postgresql+psycopg://sfm:secret@db.internal:5432/sfmapi" `
        -RedisUrl "redis://redis.internal:6379/0" `
        -GpuUuid "0"
#>

param(
    [string]$ServiceName = "sfmapi-worker",
    [string]$VenvPath = "$(Resolve-Path (Join-Path $PSScriptRoot '..\.venv'))",
    [string]$WorkingDir = "$(Resolve-Path (Join-Path $PSScriptRoot '..'))",
    [string]$DbUrl = "postgresql+psycopg://sfm:sfm@localhost:5432/sfmapi",
    [string]$RedisUrl = "redis://localhost:6379/0",
    [string]$GpuUuid = "",
    [string]$LogLevel = "INFO",
    [int]$LeaseTtlSeconds = 30
)

$ErrorActionPreference = "Stop"

function Require-Admin {
    $current = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if (-not $current.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "This script must be run as Administrator."
    }
}

function Require-Cmd($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Required command not found on PATH: $name"
    }
}

Require-Admin
Require-Cmd nssm

$pythonExe = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Could not find python.exe at $pythonExe — pass -VenvPath."
}
if (-not (Test-Path $WorkingDir)) {
    throw "Working dir not found: $WorkingDir"
}

$logDir = Join-Path $WorkingDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutLog = Join-Path $logDir "$ServiceName.stdout.log"
$stderrLog = Join-Path $logDir "$ServiceName.stderr.log"

# ARQ entrypoint:  arq sceneapi.server.workers.runner.WorkerSettings
$arqExe = Join-Path $VenvPath "Scripts\arq.exe"
if (-not (Test-Path $arqExe)) {
    throw "arq not installed in venv. Run `uv pip install -e .` first."
}

# Idempotent: remove existing service if present.
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Host "Stopping & removing existing service '$ServiceName'..."
    & nssm stop $ServiceName confirm | Out-Null
    & nssm remove $ServiceName confirm | Out-Null
}

Write-Host "Installing service '$ServiceName'..." -ForegroundColor Cyan
& nssm install $ServiceName $arqExe sceneapi.server.workers.runner.WorkerSettings
& nssm set $ServiceName AppDirectory $WorkingDir
& nssm set $ServiceName AppStdout $stdoutLog
& nssm set $ServiceName AppStderr $stderrLog
& nssm set $ServiceName AppRotateFiles 1
& nssm set $ServiceName AppRotateBytes 50000000
& nssm set $ServiceName Start SERVICE_AUTO_START

# Environment block (one VAR=VALUE per line, NUL-separated under the hood).
$envLines = @(
    "SCENEAPI_DB_URL=$DbUrl",
    "SCENEAPI_REDIS_URL=$RedisUrl",
    "SCENEAPI_PYCOLMAP_AVAILABLE=true",
    "SCENEAPI_LOG_LEVEL=$LogLevel",
    "SCENEAPI_LEASE_TTL_SECONDS=$LeaseTtlSeconds",
    "SCENEAPI_INLINE_TASKS=false",
    "PYTHONUNBUFFERED=1"
)
if ($GpuUuid) {
    $envLines += "CUDA_VISIBLE_DEVICES=$GpuUuid"
    $envLines += "SCENEAPI_WORKER_ID=$ServiceName-gpu$GpuUuid"
} else {
    $envLines += "SCENEAPI_WORKER_ID=$ServiceName"
}
& nssm set $ServiceName AppEnvironmentExtra $envLines

# Crash policy: throttle restarts to avoid hot-loop.
& nssm set $ServiceName AppExit Default Restart
& nssm set $ServiceName AppRestartDelay 5000
& nssm set $ServiceName AppThrottle 30000

Write-Host "Starting '$ServiceName'..." -ForegroundColor Cyan
& nssm start $ServiceName

Write-Host "Done. Tail logs with:" -ForegroundColor Green
Write-Host "  Get-Content -Wait '$stdoutLog'"
