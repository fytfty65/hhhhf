# OmniRoute 一键部署（本机 Windows 执行）
#
#   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -Server 118.178.237.166 -User root
#
# 做的事：打包源码 → scp 到服务器 → 远程执行 deploy.sh（备份数据库、编译网关、
# 按需装依赖、构建前端、重启三个服务、验活）。
#
# 认证：默认用 %USERPROFILE%\.ssh\id_ed25519。密钥没装到服务器时会退回密码登录
# （会提示输入一次密码，scp 与 ssh 各一次）。装密钥：ssh-copy-id root@<Server>。
param(
  [string]$Server = '118.178.237.166',
  [string]$User = 'root',
  [string]$KeyPath = "$env:USERPROFILE\.ssh\id_ed25519",
  [string]$RemoteRoot = '/opt/omniroute'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $env:TEMP 'omniroute-src.tgz'
$remoteArchive = '/tmp/omniroute-src.tgz'
$remoteScript = '/tmp/omniroute-deploy.sh'

function Invoke-Step($name, [scriptblock]$action) {
  Write-Host ""
  Write-Host "== $name" -ForegroundColor Cyan
  & $action
  if ($LASTEXITCODE -ne 0) { throw "$name 失败（exit $LASTEXITCODE）" }
}

Write-Host "仓库根：$repo"
Write-Host "目标：${User}@${Server}:$RemoteRoot"

# 认证参数：有密钥就走免交互，否则让 ssh 自己提示密码
$authArgs = @('-o', 'ConnectTimeout=15', '-o', 'StrictHostKeyChecking=accept-new')
if (Test-Path $KeyPath) {
  $authArgs += @('-i', $KeyPath, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes')
} else {
  Write-Host "未找到密钥 $KeyPath，将使用密码登录" -ForegroundColor Yellow
}

Invoke-Step "本地类型检查" {
  # 发布闸门：服务器构建时设置了 OMNI_SKIP_TYPECHECK=1（那台 1.7G 的机器跑 tsc 要 20 分钟，
  # 期间整站无响应），因此类型错误必须在这里拦住，否则会带着类型问题直接上线。
  Push-Location (Join-Path $repo 'front')
  try { & npm run typecheck } finally { Pop-Location }
}

Invoke-Step "打包源码" {
  # 排除：构建产物、依赖、以及服务器上已有的生产配置（.env / .env.local）
  # 生产配置里的域名、密钥、数据库路径不能被本地开发配置覆盖。
  & tar -czf $archive -C $repo `
    --exclude '.git' `
    --exclude '.gocache' `
    --exclude 'node_modules' `
    --exclude 'front/node_modules' `
    --exclude 'front/.next' `
    --exclude 'front/.next-build' `
    --exclude 'front/.next-old' `
    --exclude 'front/test-results' `
    --exclude 'front/playwright-report' `
    --exclude 'front/.turbo' `
    --exclude 'front/.env.local' `
    --exclude 'gateway/.env' `
    --exclude 'ai-service/.env' `
    --exclude 'gateway/data' `
    --exclude 'gateway/backups' `
    --exclude 'gateway/uploads' `
    --exclude 'gateway/*.exe' `
    --exclude 'gateway/*.db' `
    --exclude 'ai-service/.venv' `
    --exclude 'ai-service/venv' `
    --exclude 'ai-service/.mypy_cache' `
    --exclude 'ai-service/.ruff_cache' `
    --exclude 'ai-service/.pytest_cache' `
    --exclude '__pycache__' `
    --exclude 'android' `
    --exclude 'work' `
    --exclude '.uploads' `
    --exclude '*.tgz' `
    .
  $size = [math]::Round((Get-Item $archive).Length / 1MB, 2)
  Write-Host "  源码包 $size MB"
}

Invoke-Step "上传源码包" {
  & scp @authArgs $archive "${User}@${Server}:$remoteArchive"
}

Invoke-Step "上传部署脚本" {
  & scp @authArgs (Join-Path $PSScriptRoot 'deploy.sh') "${User}@${Server}:$remoteScript"
}

Invoke-Step "远程部署" {
  # sed：去掉 Windows 可能带上的 CRLF，否则 bash 会把 \r 当成命令的一部分
  $remoteCmd = "sed -i 's/\r`$//' $remoteScript && bash $remoteScript $remoteArchive"
  & ssh @authArgs "${User}@${Server}" $remoteCmd
}

Remove-Item $archive -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "部署完成：http://$Server" -ForegroundColor Green
