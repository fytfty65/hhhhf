<#
.SYNOPSIS
  幂等的本地开发环境启动器：Redis（WSL）+ ai-service(8000) + 网关(8080)。

.DESCRIPTION
  用于登录自启或手动一键拉起。已经在跑的组件会跳过，不会盲目重启。

  三个实测坑对应这里的三条逻辑：
  1. Redis 装在 WSL 里，Windows 侧没有原生服务；WSL 空闲会被回收，6379 随之消失。
     所以启动 redis-server 之后还要挂一个 `sleep infinity` 保活进程。
  2. 网关连 Redis 必须用 127.0.0.1，不能用 localhost（Windows 会先解析到 ::1）。
     该值已写在 gateway/.env。
  3. ai-service 必须用 production 模式启动，避免 reload 子进程占端口留下孤儿；
     具体重启与自检复用 scripts/restart-services.ps1。

.USAGE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev-up.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev-up.ps1 -Force
#>
param(
  [switch]$Force
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$work = Join-Path $root 'work'
if (-not (Test-Path $work)) { New-Item -ItemType Directory -Path $work | Out-Null }

function Test-Port([int]$port) {
  $client = New-Object System.Net.Sockets.TcpClient
  try { $client.Connect('127.0.0.1', $port); return $true } catch { return $false } finally { $client.Close() }
}

function Test-Url([string]$url) {
  try { Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing | Out-Null; return $true } catch { return $false }
}

# ---- 1. Redis（WSL）----
if ($Force -or -not (Test-Port 6379)) {
  Write-Host '==> 启动 WSL Redis'
  wsl -d Ubuntu -u root -- bash -lc "service redis-server start" 2>$null | Out-Null
  $keepalive = (wsl -d Ubuntu -u root -- bash -lc "pgrep -f 'sleep infinity' | head -n1" 2>$null)
  if (-not $keepalive) {
    Write-Host '    挂载 WSL 保活进程（防止空闲回收后 6379 消失）'
    Start-Process -FilePath 'wsl.exe' -ArgumentList '-d', 'Ubuntu', '-u', 'root', '--', 'sleep', 'infinity' -WindowStyle Hidden
  }
  for ($i = 0; $i -lt 60; $i++) {
    if (Test-Port 6379) { break }
    Start-Sleep -Milliseconds 500
  }
  if (Test-Port 6379) { Write-Host '    Redis 6379 就绪' } else { Write-Warning '    Redis 仍未就绪，检查 WSL 是否可用：wsl -d Ubuntu -u root -- redis-cli ping' }
} else {
  Write-Host '==> Redis 已在运行（6379）'
}

# ---- 2. 网关 + ai-service ----
$gatewayOk = Test-Url 'http://127.0.0.1:8080/healthz'
$aiOk = Test-Url 'http://127.0.0.1:8000/health'
if ($Force -or -not ($gatewayOk -and $aiOk)) {
  Write-Host '==> 启动网关 / ai-service'
  & (Join-Path $PSScriptRoot 'restart-services.ps1')
} else {
  Write-Host '==> 网关与 ai-service 已在运行'
}

# ---- 3. 汇总 ----
Write-Host ''
Write-Host '---- 当前状态 ----'
Write-Host ("Redis   6379 : " + $(if (Test-Port 6379) { 'ok' } else { 'down' }))
Write-Host ("Gateway      : " + $(if (Test-Url 'http://127.0.0.1:8080/healthz') { 'ok' } else { 'down' }))
Write-Host ("ai-service   : " + $(if (Test-Url 'http://127.0.0.1:8000/health') { 'ok' } else { 'down' }))
Write-Host '日志：work/gateway.out.log · work/gateway.err.log · work/ai-service.out.log · work/ai-service.err.log'
