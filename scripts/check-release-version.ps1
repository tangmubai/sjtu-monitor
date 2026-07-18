[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$package = Get-Content -LiteralPath (Join-Path $root "package.json") -Raw | ConvertFrom-Json
$tauri = Get-Content -LiteralPath (Join-Path $root "src-tauri/tauri.conf.json") -Raw | ConvertFrom-Json
$cargoVersion = (Select-String -LiteralPath (Join-Path $root "src-tauri/Cargo.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1).Matches.Groups[1].Value

$versions = @($package.version, $tauri.version, $cargoVersion)
if ($versions | Where-Object { $_ -notmatch '^\d+\.\d+\.\d+$' }) {
  throw "发布版本必须是三段数字，当前值：$($versions -join ', ')"
}
if (($versions | Select-Object -Unique).Count -ne 1) {
  throw "package.json、tauri.conf.json 与 Cargo.toml 的版本必须一致：$($versions -join ', ')"
}

Write-Host "Release version verified: $($package.version); MSIX version: $($package.version).0"
