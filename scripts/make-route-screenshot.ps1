# 生成一张"小红书风格"的路线截图，用于验证参考路线截图识别（真实视觉模型调用）。
#
# 为什么要在仓库里留一个造图脚本：OCR 是典型"只有真图才能证伪"的功能 ——
# 单测只能覆盖校验/提示词/解析，识别质量必须拿真图跑一遍。
# 图形栈用 System.Drawing（Windows 自带），不引入任何前端/Node 依赖。
#
# 用法（必须用 pwsh 7 跑：Windows PowerShell 5.1 会把无 BOM 的 UTF-8 脚本读成乱码）：
#   pwsh -File scripts/make-route-screenshot.ps1 -Out work/route-screenshot.png

param(
  [string]$Out = "work/route-screenshot.png"
)

Add-Type -AssemblyName System.Drawing

$width = 940
$height = 700
$bitmap = New-Object System.Drawing.Bitmap($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.Clear([System.Drawing.Color]::White)
$graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit

$titleFont = New-Object System.Drawing.Font('Microsoft YaHei', 26, [System.Drawing.FontStyle]::Bold)
$bodyFont = New-Object System.Drawing.Font('Microsoft YaHei', 20)
$black = [System.Drawing.Brushes]::Black
$gray = [System.Drawing.Brushes]::DimGray

$graphics.DrawString('库尔勒2天1夜｜人均300｜亲测不踩雷', $titleFont, $black, 40, 30)
$graphics.DrawString('（截图识别测试图 · 非真实帖子）', $bodyFont, $gray, 40, 78)

$lines = @(
  'Day1 上午 博斯腾湖 环湖骑行 门票45元',
  'Day1 中午 湖边烤鱼 人均60元',
  'Day1 下午 铁门关 峡谷徒步 门票30元',
  'Day1 晚上 库尔勒老街夜市 烤包子10元',
  'Day2 上午 罗布人村寨 看胡杨林 门票50元',
  'Day2 下午 孔雀河景观带 慢走 免费',
  '住宿 孔雀河边民宿 一晚180元',
  '交通 市区打车+包车 一天200元',
  '节奏 轻松 不赶路 每天4个点'
)

$y = 140
foreach ($line in $lines) {
  $graphics.DrawString($line, $bodyFont, $black, 40, $y)
  $y += 52
}

$target = if ([System.IO.Path]::IsPathRooted($Out)) { $Out } else { Join-Path (Get-Location).Path $Out }
$directory = Split-Path -Parent $target
if ($directory -and -not (Test-Path $directory)) {
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
$bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()

$info = Get-Item $target
Write-Host "已生成截图: $($info.FullName) ($([math]::Round($info.Length / 1024, 1))KB)"
