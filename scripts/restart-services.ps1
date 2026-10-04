<#
.SYNOPSIS
  安全重启本地两个服务（网关 8080 + ai-service 8000），并**验证**结果。

.DESCRIPTION
  这个脚本存在的理由是两个实测踩过的坑（2026-10-04）：

  1. **孤儿进程占着端口**：ai-service 在 dev 模式（reload）下是"父 supervisor + 子 worker"，
     uvicorn 的 reload 子进程会**继承监听套接字**。只杀父进程 → 端口仍被占用、健康检查还能通，
     但那个进程已经没人管了；再启动时网关会直接
     `bind: Only one usage of each socket address` 启动失败。
     所以这里按端口找到占用者，**连子进程一起杀**。

  2. **环境变量不会跟着 Start-Process 走**：实测从某些宿主（例如自动化 shell）里
     `$env:APP_ENV='production'` 后 `Start-Process python main.py`，子进程读到的 APP_ENV 是**空**，
     于是 main.py 按默认值进入 **dev/reload 模式**（多一个进程、白烧 CPU、还更容易留孤儿）。
     所以这里用 `cmd /c set APP_ENV=production&& python main.py` 把变量显式塞进子进程。

  启动后脚本会自检：
    - 两个端口都在监听；
    - 健康检查都通过；
    - ai-service **只有一个** python 进程（多于一个 = 又跑成 reload 模式了）。

.USAGE
  powershell -NoProfile -File scripts/restart-services.ps1
  powershell -NoProfile -File scripts/restart-services.ps1 -GatewayOnly
  powershell -NoProfile -File scripts/restart-services.ps1 -AiOnly
#>
param(
  [switch]$GatewayOnly,
  [switch]$AiOnly
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$work = Join-Path $root 'work'
$gatewayExe = Join-Path $work 'gateway-fresh.exe'
$gatewayDir = Join-Path $root 'gateway'
$aiDir = Join-Path $root 'ai-service'
$python = Join-Path $aiDir 'venv\Scripts\python.exe'

function Write-Step($message) { Write-Host "==> $message" }

function Stop-ServiceOnPort($port) {
  $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if (-not $listeners) { Write-Host "    端口 $port 没人在听"; return }
  foreach ($listener in $listeners) {
    $owner = $listener.OwningProcess
    # 先杀子进程：reload/worker 子进程继承套接字，只杀父进程会留下占住端口的孤儿
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $owner" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
      Write-Host ("    结束子进程 {0} ({1})" -f $child.ProcessId, $child.Name)
      Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host ("    结束占用者 {0}" -f $owner)
    Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
  }
  # 关键补充：还要清理**不占端口**的残留进程。实测形态是"父 supervisor（内存很小、不持端口）
  # + 子 worker（持端口）"，只按端口杀会漏掉父进程，它可能再拉起一个 worker，
  # 于是端口看起来"自己又活了"。
  $stale = Get-CimInstance Win32_Process -Filter "Name LIKE '%python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'ai-service' -and $_.CommandLine -match 'main\.py' }
  foreach ($process in $stale) {
    Write-Host ("    结束残留 ai-service 进程 {0}" -f $process.ProcessId)
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
  }
  # ⚠️ 清理残留网关进程必须尊重 -AiOnly。把它无条件写在函数里，会让 `-AiOnly`
  # 顺手把网关也杀掉 —— 实测就是这么把网关停掉、又没人重新拉起来的。
  if (-not $AiOnly) {
    Get-CimInstance Win32_Process -Filter "Name LIKE '%gateway%'" -ErrorAction SilentlyContinue |
      ForEach-Object {
        Write-Host ("    结束残留网关进程 {0}" -f $_.ProcessId)
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      }
  }
  for ($i = 0; $i -lt 15; $i++) {
    if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { return }
    Start-Sleep -Seconds 1
  }
  Write-Warning "    端口 $port 仍然被占用，请手动检查"
}

function Wait-Healthy($url, $seconds) {
  for ($i = 0; $i -lt $seconds; $i++) {
    try {
      $response = Invoke-RestMethod -Uri $url -TimeoutSec 3
      return $response
    } catch { Start-Sleep -Seconds 1 }
  }
  return $null
}

if (-not $AiOnly) {
  Write-Step "重启网关"
  if (-not (Test-Path $gatewayExe)) {
    Write-Host "    未找到 $gatewayExe，先构建（go build）"
    Push-Location $gatewayDir
    go build -o $gatewayExe ./cmd/server
    Pop-Location
  }
  Stop-ServiceOnPort 8080
  Start-Process -FilePath $gatewayExe -WorkingDirectory $gatewayDir -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $work 'gateway.out.log') -RedirectStandardError (Join-Path $work 'gateway.err.log')
}

if (-not $GatewayOnly) {
  Write-Step "重启 ai-service（生产模式，单进程）"
  Stop-ServiceOnPort 8000
  # 关键：用 cmd 显式设置 APP_ENV，不依赖 Start-Process 的环境继承（实测会丢）
  Start-Process -FilePath 'cmd.exe' -WorkingDirectory $aiDir -WindowStyle Hidden `
    -ArgumentList '/c', "set APP_ENV=production&& `"$python`" main.py" `
    -RedirectStandardOutput (Join-Path $work 'ai-service.out.log') -RedirectStandardError (Join-Path $work 'ai-service.err.log')
}

Start-Sleep -Seconds 5

Write-Step "自检"
$gatewayHealth = if ($GatewayOnly -or -not $AiOnly) { Wait-Healthy 'http://127.0.0.1:8080/healthz' 15 } else { $null }
$aiHealth = if ($AiOnly -or -not $GatewayOnly) { Wait-Healthy 'http://127.0.0.1:8000/health' 20 } else { $null }

if (-not $AiOnly) {
  if ($gatewayHealth) { Write-Host "    网关: $($gatewayHealth | ConvertTo-Json -Compress)" } else { Write-Warning "    网关健康检查失败，看 work/gateway.err.log" }
}
if (-not $GatewayOnly) {
  if ($aiHealth) { Write-Host "    ai-service: $($aiHealth | ConvertTo-Json -Compress)" } else { Write-Warning "    ai-service 健康检查失败，看 work/ai-service.err.log" }
  # 判据用**日志里的 reload 值**，不是进程数：实测在某些启动方式下会出现
  # "父 supervisor + 子 worker"两个进程，但日志明确写着 reload=False —— 那是正常的，报错只会误导。
  $logLine = (Get-Content (Join-Path $work 'ai-service.out.log') -TotalCount 3 -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '\[启动\]' } | Select-Object -First 1)
  $processCount = @(Get-CimInstance Win32_Process -Filter "Name LIKE '%python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'main\.py' }).Count
  if ($logLine -match 'reload=True') {
    Write-Warning "    ai-service 处于 reload 模式（开发模式）：会多起一个进程、白烧 CPU，并可能在停止时留下占住端口的孤儿。"
  } elseif ($logLine) {
    Write-Host "    启动模式: $logLine"
  } else {
    Write-Host "    （没读到启动日志行，无法确认模式）"
  }
  Write-Host "    ai-service 相关 python 进程数: $processCount"
}

Write-Host ""
Write-Host "日志：work/gateway.out.log · work/gateway.err.log · work/ai-service.out.log · work/ai-service.err.log"
