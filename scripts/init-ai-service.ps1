<#
.SYNOPSIS
    One-click setup for the AI service on Windows.

.DESCRIPTION
    Creates the pinned `ai-service/.venv`, installs the locked dependencies and
    runs the preflight check. Safe to re-run: an existing .venv is reused, and
    pip only installs what is missing.

    The interpreter version comes from `ai-service/.python-version`, which is
    also what CI reads. Keeping one file as the source of truth is what stops
    "passes locally, fails in CI" drift.

.PARAMETER PythonCommand
    Interpreter to create the venv with. Defaults to `py -3.14`, then `python`.

.PARAMETER RuntimeOnly
    Install requirements.txt instead of requirements-dev.txt (no linters or
    scanners). Use this on a deployment host; developers need the dev file.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/init-ai-service.ps1
#>
[CmdletBinding()]
param(
    [string]$PythonCommand = "",
    [switch]$RuntimeOnly
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$aiRoot = Join-Path $repoRoot "ai-service"
$venvPath = Join-Path $aiRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$versionFile = Join-Path $aiRoot ".python-version"

if (-not (Test-Path $versionFile)) {
    throw "Missing $versionFile; it is the pinned interpreter version."
}
$pinned = (Get-Content $versionFile -Raw).Trim()

function Invoke-Native {
    param(
        [string]$Exe,
        [string[]]$Arguments
    )
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Resolve-Python {
    param([string]$Requested)

    $attempts = @()
    if ($Requested) {
        $attempts += , @($Requested, @())
    }
    $attempts += , @("py", @("-$pinned"))
    $attempts += , @("python", @())

    foreach ($attempt in $attempts) {
        $exe = [string]$attempt[0]
        $prefix = [string[]]$attempt[1]
        $version = $null
        try {
            $version = & $exe @prefix -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
        }
        catch {
            continue
        }
        if ($LASTEXITCODE -ne 0 -or -not $version) {
            continue
        }
        $version = $version.Trim()
        if ($version -eq $pinned) {
            return @($exe, $prefix)
        }
        Write-Host "Skipping $exe ($version): this project is pinned to $pinned."
    }
    return $null
}

$resolved = Resolve-Python -Requested $PythonCommand
if (-not $resolved) {
    throw "No Python $pinned found. Install it (https://www.python.org/downloads/) or pass -PythonCommand."
}
$baseExe = [string]$resolved[0]
$basePrefix = [string[]]$resolved[1]

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating $venvPath with Python $pinned ..."
    Invoke-Native -Exe $baseExe -Arguments ($basePrefix + @("-m", "venv", $venvPath))
}
else {
    Write-Host "Reusing existing $venvPath"
}

Invoke-Native -Exe $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip")

$requirements = if ($RuntimeOnly) { "requirements.txt" } else { "requirements-dev.txt" }
Write-Host "Installing $requirements ..."
Push-Location $aiRoot
try {
    Invoke-Native -Exe $venvPython -Arguments @("-m", "pip", "install", "-r", $requirements)
    Invoke-Native -Exe $venvPython -Arguments @("-m", "core.preflight")
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "AI service environment is ready."
Write-Host "  interpreter : $venvPython"
Write-Host "  start       : cd ai-service; .venv\Scripts\python.exe main.py"
Write-Host "  run checks  : cd ai-service; .venv\Scripts\python.exe tools\check.py"
