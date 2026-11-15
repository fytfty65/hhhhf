param([string]$Blender = 'blender')
$root = Split-Path -Parent $PSScriptRoot
& $Blender -b --python (Join-Path $PSScriptRoot 'build_spring_hero.py')
if ($LASTEXITCODE -ne 0) { throw "Blender render failed with exit code $LASTEXITCODE" }
Write-Host 'Spring hero exported to front/public/hero/v2'
