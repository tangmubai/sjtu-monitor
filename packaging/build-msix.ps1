[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Version,

  [ValidateSet("x64", "arm64")]
  [string]$Architecture = "x64",

  [string]$ReleaseDir = "src-tauri/target/release",

  [string]$OutputPath = "src-tauri/target/release/bundle/msix/交我选.msix",

  [string]$CertificatePath,

  [string]$CertificatePassword,

  [switch]$SkipSign
)

$ErrorActionPreference = "Stop"

function Find-WindowsSdkTool([string]$ToolName) {
  $command = Get-Command $ToolName -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }

  $roots = @(
    (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"),
    (Join-Path ${env:ProgramFiles} "Windows Kits\10\bin")
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
  foreach ($root in $roots) {
    $match = Get-ChildItem -LiteralPath $root -Recurse -Filter $ToolName -File -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -match "\\$Architecture\\" } |
      Sort-Object FullName -Descending |
      Select-Object -First 1
    if ($match) { return $match.FullName }
  }
  throw "未找到 $ToolName。请安装 Windows 10/11 SDK，并确保其 bin 目录可访问。"
}

if ($Version -notmatch '^\d+\.\d+\.\d+\.\d+$') {
  throw "MSIX 版本必须是四段数字，例如 0.4.1.0；收到：$Version"
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releaseRoot = Join-Path $projectRoot $ReleaseDir
$mainExe = Join-Path $releaseRoot "sjtu-monitor-desktop.exe"
$resources = Join-Path $releaseRoot "resources"
$template = Join-Path $PSScriptRoot "Package.appxmanifest.template"
$output = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))
$outputDirectory = Split-Path -Parent $output
$stage = Join-Path $outputDirectory ".msix-stage-$Architecture"

if (-not (Test-Path -LiteralPath $mainExe)) { throw "未找到 Tauri Release 主程序：$mainExe" }
if (-not (Test-Path -LiteralPath $resources)) { throw "未找到打包资源目录：$resources" }

$makeAppx = Find-WindowsSdkTool "MakeAppx.exe"
$signTool = if ($SkipSign) { $null } else { Find-WindowsSdkTool "SignTool.exe" }

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

try {
  Copy-Item -LiteralPath $mainExe -Destination $stage
  Copy-Item -LiteralPath $resources -Destination (Join-Path $stage "resources") -Recurse

  $assets = Join-Path $stage "Assets"
  New-Item -ItemType Directory -Force -Path $assets | Out-Null
  $iconRoot = Join-Path $projectRoot "src-tauri/icons"
  foreach ($icon in @("StoreLogo.png", "Square44x44Logo.png", "Square150x150Logo.png", "Square310x310Logo.png")) {
    Copy-Item -LiteralPath (Join-Path $iconRoot $icon) -Destination $assets
  }
  Add-Type -AssemblyName System.Drawing
  $squareLogo = [System.Drawing.Image]::FromFile((Join-Path $assets "Square310x310Logo.png"))
  $wideLogo = [System.Drawing.Bitmap]::new(310, 150)
  $graphics = [System.Drawing.Graphics]::FromImage($wideLogo)
  try {
    $graphics.Clear([System.Drawing.Color]::FromArgb(7, 89, 133))
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.DrawImage($squareLogo, [System.Drawing.Rectangle]::new(80, 0, 150, 150))
    $wideLogo.Save((Join-Path $assets "Wide310x150Logo.png"), [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    $graphics.Dispose()
    $wideLogo.Dispose()
    $squareLogo.Dispose()
  }

  $manifest = (Get-Content -LiteralPath $template -Raw).
    Replace("__MSIX_VERSION__", $Version).
    Replace("__ARCHITECTURE__", $Architecture)
  [System.IO.File]::WriteAllText((Join-Path $stage "AppxManifest.xml"), $manifest, [System.Text.UTF8Encoding]::new($false))

  if (Test-Path -LiteralPath $output) { Remove-Item -LiteralPath $output -Force }
  & $makeAppx pack /d $stage /p $output /o | Write-Host
  if ($LASTEXITCODE -ne 0) { throw "MakeAppx 打包失败，退出码：$LASTEXITCODE" }

  if (-not $SkipSign) {
    if (-not $CertificatePath -or -not $CertificatePassword) {
      throw "Store 产物必须签名。请提供 -CertificatePath 与 -CertificatePassword，或仅用于结构检查时传入 -SkipSign。"
    }
    & $signTool sign /fd SHA256 /f $CertificatePath /p $CertificatePassword $output | Write-Host
    if ($LASTEXITCODE -ne 0) { throw "SignTool 签名失败，退出码：$LASTEXITCODE" }
  }

  Write-Host "MSIX 已生成：$output"
} finally {
  if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
