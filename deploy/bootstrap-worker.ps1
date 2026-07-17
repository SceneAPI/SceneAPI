<#
.SYNOPSIS
    One-shot bootstrap for an sfmapi worker host on Windows + CUDA.

.DESCRIPTION
    Designed to ship inside `worker-installer.zip`. Walks an operator
    through the full install:

      1. Verify prerequisites (Python 3.12, uv, git, nssm, NVIDIA CUDA).
      2. Clone or update `sfmapi` and `colmap_mod` next to each other.
      3. Create a venv, install sfmapi[dev], build & install pycolmap
         from the configured `colmap_mod` ref.
      4. Apply `uv.lock` (if shipped in the zip) for deterministic
         dependency resolution.
      5. Register the worker as a Windows service via
         `install-worker.ps1`, pointing at the supplied DB / Redis URLs.

    Re-running is safe — it skips finished steps when their state is
    already correct.

.PARAMETER InstallRoot
    Directory under which `sfmapi/` and `colmap_mod/` will live.
    Default: C:\sfmapi.

.PARAMETER ColmapModRef
    Git ref of colmap_mod to build (branch / tag / sha).
    Default: main.

.PARAMETER DbUrl
    SCENEAPI_DB_URL the worker will use.

.PARAMETER RedisUrl
    SCENEAPI_REDIS_URL the worker will use.

.PARAMETER GpuUuid
    CUDA_VISIBLE_DEVICES value (e.g. "0"). Optional.

.PARAMETER ServiceName
    Windows service name. Default: sfmapi-worker.

.PARAMETER SkipBuild
    Skip the (slow) pycolmap build step. Useful when re-registering an
    existing service.

.EXAMPLE
    .\bootstrap-worker.ps1 `
        -InstallRoot C:\sfmapi `
        -DbUrl "postgresql+psycopg://sfm:secret@db.internal:5432/sfmapi" `
        -RedisUrl "redis://redis.internal:6379/0" `
        -GpuUuid "0"
#>

param(
    [string]$InstallRoot = "C:\sfmapi",
    [string]$ColmapModRef = "main",
    [string]$DbUrl = "postgresql+psycopg://sfm:sfm@localhost:5432/sfmapi",
    [string]$RedisUrl = "redis://localhost:6379/0",
    [string]$GpuUuid = "",
    [string]$ServiceName = "sfmapi-worker",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Done($msg)  { Write-Host "    $msg"  -ForegroundColor Green }
function Note($msg)  { Write-Host "    $msg"  -ForegroundColor Yellow }
function Require-Cmd($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Required command not found on PATH: $name"
    }
}
function Require-Admin {
    $current = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if (-not $current.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "bootstrap-worker.ps1 must run as Administrator (nssm needs it)."
    }
}

Step "Prereq checks"
Require-Admin
Require-Cmd git
Require-Cmd python
Require-Cmd uv
Require-Cmd nssm
$pyVer = (& python --version) 2>&1
if ($pyVer -notmatch "3\.12") {
    Note "Detected $pyVer; expected Python 3.12.x. Continuing, but this is the supported version."
}
& nvidia-smi | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "nvidia-smi failed; CUDA driver missing on this host."
}
Done "all prereqs present"

Step "Ensure install root exists: $InstallRoot"
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null

$SfmDir = Join-Path $InstallRoot "sfmapi"
$ColmapDir = Join-Path $InstallRoot "colmap_mod"

function Sync-Repo {
    param([string]$Url, [string]$Path, [string]$Ref)
    if (Test-Path (Join-Path $Path ".git")) {
        Step "Updating $(Split-Path $Path -Leaf) -> $Ref"
        & git -C $Path fetch --all --tags --prune | Out-Null
        & git -C $Path checkout $Ref | Out-Null
        & git -C $Path pull --ff-only 2>$null | Out-Null
    } else {
        Step "Cloning $Url -> $Path ($Ref)"
        & git clone $Url $Path
        if ($Ref -ne "main") {
            & git -C $Path checkout $Ref | Out-Null
        }
    }
    & git -C $Path submodule update --init --recursive | Out-Null
    Done "$(Split-Path $Path -Leaf) at $(& git -C $Path rev-parse --short HEAD)"
}

# These two URLs are owner-relative — operators using a fork should
# override via env before running. Defaults match the canonical org.
$SfmRepoUrl = if ($env:SCENEAPI_REPO_URL) { $env:SCENEAPI_REPO_URL } else { "https://github.com/sfmapi/sfmapi.git" }
$ColmapRepoUrl = if ($env:COLMAP_MOD_REPO_URL) { $env:COLMAP_MOD_REPO_URL } else { "https://github.com/opsiclear/colmap_mod.git" }

Sync-Repo -Url $SfmRepoUrl -Path $SfmDir -Ref "main"
Sync-Repo -Url $ColmapRepoUrl -Path $ColmapDir -Ref $ColmapModRef

# If a uv.lock shipped in the zip, copy it into the sfmapi tree so the
# venv resolves identical versions to what the release was tested with.
$ShippedLock = Join-Path $ScriptDir "uv.lock"
if (Test-Path $ShippedLock) {
    Step "Applying shipped uv.lock"
    Copy-Item $ShippedLock (Join-Path $SfmDir "uv.lock") -Force
    Done "lockfile in place"
}

Push-Location $SfmDir
try {
    Step "Creating venv"
    if (-not (Test-Path ".venv")) {
        & uv venv --seed
    } else {
        Done ".venv already present"
    }

    Step "Installing sfmapi[dev]"
    & uv pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { throw "sfmapi install failed" }

    if (-not $SkipBuild) {
        Step "Building & installing pycolmap from $ColmapDir (this is slow)"
        $env:CMAKE_GENERATOR = "Ninja"
        & uv pip install -e $ColmapDir --no-build-isolation -v
        if ($LASTEXITCODE -ne 0) { throw "pycolmap build failed" }
        Done "pycolmap installed"
    } else {
        Note "Skipping pycolmap build (-SkipBuild)"
    }

    Step "Registering Windows service via install-worker.ps1"
    $installScript = Join-Path $ScriptDir "install-worker.ps1"
    if (-not (Test-Path $installScript)) {
        $installScript = Join-Path $SfmDir "deploy\install-worker.ps1"
    }
    if (-not (Test-Path $installScript)) {
        throw "install-worker.ps1 not found in zip or repo"
    }
    & powershell -ExecutionPolicy Bypass -File $installScript `
        -ServiceName $ServiceName `
        -VenvPath (Join-Path $SfmDir ".venv") `
        -WorkingDir $SfmDir `
        -DbUrl $DbUrl `
        -RedisUrl $RedisUrl `
        -GpuUuid $GpuUuid
    if ($LASTEXITCODE -ne 0) { throw "install-worker.ps1 failed" }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "=== bootstrap complete ===" -ForegroundColor Green
Write-Host "Service name: $ServiceName"
Write-Host "Install root: $InstallRoot"
Write-Host "DB URL:       $DbUrl"
Write-Host "Redis URL:    $RedisUrl"
Write-Host ""
Write-Host "Tail the worker:"
Write-Host "  Get-Content -Wait '$SfmDir\logs\$ServiceName.stdout.log'"
