param(
  [Parameter(Mandatory = $true)][string]$Database,
  [Parameter(Mandatory = $false)][string]$ModelVersion = $env:MODEL_EVALUATION_MODEL_VERSION
)

$ErrorActionPreference = 'Stop'
$serviceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $serviceRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  throw "AI service virtual environment not found: $python"
}
if (-not $ModelVersion) {
  throw "ModelVersion or MODEL_EVALUATION_MODEL_VERSION is required"
}

Push-Location $serviceRoot
try {
  & $python -m tools.run_staging_smoke --db $Database --model-version $ModelVersion
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
  Pop-Location
}
