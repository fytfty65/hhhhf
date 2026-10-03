param(
  [Parameter(Mandatory = $true)][string]$Database,
  [Parameter(Mandatory = $true)][string]$ModelId,
  [Parameter(Mandatory = $true)][string]$ModelVersion,
  [string]$GatewayUrl = $env:MODEL_EVALUATION_GATEWAY_URL,
  [string]$AuditFile = $(Join-Path $PSScriptRoot '..\work\model-evaluation.jsonl'),
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$serviceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $serviceRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  throw "AI service virtual environment not found: $python"
}

$arguments = @('-m', 'tools.run_model_evaluation', '--db', $Database, '--model-id', $ModelId, '--model-version', $ModelVersion, '--audit-file', $AuditFile)
if ($GatewayUrl) { $arguments += @('--gateway-url', $GatewayUrl) }
if ($DryRun) { $arguments += '--dry-run' }

Push-Location $serviceRoot
try {
  & $python @arguments
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
  Pop-Location
}
