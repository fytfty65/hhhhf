<#
.SYNOPSIS
  ContextualLobby.tsx 拆分门禁（拆分任务的唯一验证入口）。

.DESCRIPTION
  依次执行：tsc --noEmit → npm run test → npm run build → npx playwright test
  然后打印「首屏体积 / 组件行数」与拆分基线的对比，最后给出 PASS / FAIL。
  全过程写入 <repo>/work/split-gate.log，即使 DSH 中途崩溃，日志仍可用于复查。

  基线（2026-09-16 拆分前）：
    ContextualLobby.tsx   3,991 行 / 218,225 B
    首屏 JS               1,105.3 KB（13 chunk）（2026-09-17 经用户确认由 1,104.6 更新两次：
                          核对面板 + 面板四段新数据；面板本体按需加载）
    首屏 CSS              141.1 KB
  验收标准：拆分后首屏不得变差，且所有门禁全绿。

.USAGE
  powershell -NoProfile -File front/scripts/split-gate.ps1            # 全套（提交前跑这个）
  powershell -NoProfile -File front/scripts/split-gate.ps1 -Quick     # 只跑 tsc + 单测（秒级自检）
  powershell -NoProfile -File front/scripts/split-gate.ps1 -SkipE2E   # 跳过 E2E（构建仍跑）
  powershell -NoProfile -File front/scripts/split-gate.ps1 -SkipBuild # 跳过构建与 E2E
  （pwsh 7 同样可用：把 powershell 换成 pwsh）
#>
param(
  [switch]$Quick,
  [switch]$SkipE2E,
  [switch]$SkipBuild
)

$ErrorActionPreference = 'Continue'

# ---- 路径 ----
$front = $PSScriptRoot | Split-Path -Parent          # .../front
$repo = $front | Split-Path -Parent                 # .../OminRoute
$logDir = Join-Path $repo 'work'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'split-gate.log'
Set-Content -LiteralPath $log -Value "==== split-gate $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====" -Encoding UTF8

# Playwright 浏览器装在仓库内，避免依赖用户级缓存
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $repo 'work\ms-playwright'

$results = [ordered]@{}
function Write-Log([string]$text) {
  Write-Host $text
  Add-Content -LiteralPath $log -Value $text -Encoding UTF8
}
function Invoke-Step([string]$name, [scriptblock]$body) {
  Write-Log ""
  Write-Log "=== $name ==="
  $out = & $body 2>&1 | Out-String
  $code = $LASTEXITCODE
  $text = $out.Trim()
  if ($text) { Write-Log $text }
  $script:results[$name] = $code
  Write-Log ("--- {0}: exit {1} ---" -f $name, $code)
}

Push-Location $front

Invoke-Step 'tsc --noEmit' { npx tsc --noEmit --pretty false }

Invoke-Step 'npm run test' { npm run test }

if (-not $Quick -and -not $SkipBuild) {
  Invoke-Step 'npm run build' { npm run build }
}

if (-not $Quick -and -not $SkipBuild -and -not $SkipE2E) {
  Invoke-Step 'playwright test' { npx playwright test }
}

# ---- 体积与行数对比（与拆分基线同一算法） ----
Write-Log ""
Write-Log "=== 体积 / 行数对比（基线：3,991 行 / 1,105.3 KB JS / 141.1 KB CSS） ==="
$lobby = Join-Path $front 'app\ContextualLobby.tsx'
if (Test-Path $lobby) {
  $lines = [System.IO.File]::ReadAllLines($lobby).Length
  Write-Log ("  ContextualLobby.tsx: {0} 行, {1} B（基线 3991 行 / 218225 B）" -f $lines, (Get-Item $lobby).Length)
}

$indexHtml = Join-Path $front '.next\server\app\index.html'
if (Test-Path $indexHtml) {
  $html = Get-Content $indexHtml -Raw
  $scripts = [regex]::Matches($html, 'src="([^"]+\.js)"') | ForEach-Object { $_.Groups[1].Value }
  $jsTotal = 0
  foreach ($sc in $scripts) {
    $leaf = Split-Path $sc -Leaf
    $p = Get-ChildItem (Join-Path $front '.next\static') -Recurse -Filter $leaf -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($p) { $jsTotal += $p.Length }
  }
  Write-Log ("  首屏 JS 合计: {0:N1} KB（{1} 个 chunk）" -f ($jsTotal / 1KB), $scripts.Count)

  $css = [regex]::Matches($html, 'href="([^"]+\.css)"') | ForEach-Object { $_.Groups[1].Value }
  $cssTotal = 0
  foreach ($cf in $css) {
    $leaf = Split-Path $cf -Leaf
    $p = Get-ChildItem (Join-Path $front '.next\static') -Recurse -Filter $leaf -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($p) { $cssTotal += $p.Length }
  }
  Write-Log ("  首屏 CSS 合计: {0:N1} KB" -f ($cssTotal / 1KB))
  Write-Log "  （首屏 JS 不得超过基线 1,105.3 KB；变大即为劣化，需回滚该批）"
} else {
  Write-Log "  （未找到 .next/server/app/index.html，跳过一次构建产物体积对比）"
}

Pop-Location

# ---- 汇总 ----
$failed = @($results.GetEnumerator() | Where-Object { $_.Value -ne 0 })
Write-Log ""
Write-Log "=== 汇总 ==="
foreach ($kv in $results.GetEnumerator()) {
  Write-Log ("  {0,-16} exit {1}" -f $kv.Key, $kv.Value)
}
if ($failed.Count -eq 0) {
  Write-Log "PASS - 门禁全绿，可以提交: refactor(split): extract <Component>"
  exit 0
} else {
  Write-Log ("FAIL - 未通过: {0}" -f (($failed | ForEach-Object { $_.Key }) -join ', '))
  Write-Log "回滚本批: git checkout -- front/app/ContextualLobby.tsx; Remove-Item <新组件文件>"
  exit 1
}
