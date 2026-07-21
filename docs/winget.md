# 提交到 winget

有两条独立的路径，通常**只需要其一**。

## 方式 A（最省事）：随 Microsoft Store 自动进入 winget

上架 Microsoft Store 后，应用会自动出现在 winget 的 `msstore` 源，用户无需社区仓库 PR 即可安装：

```powershell
winget install "交我选" --source msstore
```

只有当你想进入**默认的社区源**（`winget`，指向 GitHub 上的 MSI）时，才需要方式 B。

## 方式 B：向 microsoft/winget-pkgs 提交 manifest

### 本仓库中的 manifest

`packaging/winget/<版本>/` 下维护三段式 manifest，作为社区仓库的源：

- `SJTU.SJTUMonitor.yaml`（version）
- `SJTU.SJTUMonitor.installer.yaml`（installer）
- `SJTU.SJTUMonitor.locale.en-US.yaml`（defaultLocale）

`PackageIdentifier` 为 `SJTU.SJTUMonitor`（已确认 winget-pkgs 中 `manifests/s/SJTU/` 尚不存在，无冲突）。它们最终会放到 winget-pkgs 仓库的：

```
manifests/s/SJTU/SJTUMonitor/<版本>/
```

### 托管位置

安装包托管在 **Cloudflare R2**（bucket `sjtu-monitor-dl`），通过自定义域
`dl.sj-tu.com` 对外提供，非 GitHub Release。0.5.1 的 InstallerUrl：

```
https://dl.sj-tu.com/sjtu-monitor/v0.5.1/SJTU-Monitor_0.5.1_x64_zh-CN.msi
```

`0.5.1/installer.yaml` 已按此地址填好真实值（已联网核对：HTTP 200、字节数与本地一致）：

- InstallerSha256 `711090EF17BFE55A08BA107B426D22C8133F1E3C166C7C5AC2B7E2FF066DFFAD`
- ProductCode `{58060820-0F0C-4A48-A235-C90013261F1E}`
- UpgradeCode `{CABD77EB-6E5A-5C98-B84E-BCB9FCE36031}`（跨版本升级检测用）

### 发新版本时如何刷新这些值

每次发布新 MSI（上传到 R2 的 `v<版本>/` 路径）后：

1. **InstallerSha256**：
   ```powershell
   (Get-FileHash .\SJTU-Monitor_<版本>_x64_zh-CN.msi -Algorithm SHA256).Hash
   ```
2. **ProductCode**（每版可能变；UpgradeCode 稳定不变）：
   ```powershell
   $i = New-Object -ComObject WindowsInstaller.Installer
   $db = $i.OpenDatabase("SJTU-Monitor_<版本>_x64_zh-CN.msi", 0)
   $v = $db.OpenView("SELECT Value FROM Property WHERE Property='ProductCode'")
   $v.Execute(); $v.Fetch().StringData(1)
   ```
   注意 MSI 内部 `ProductVersion` 必须与文件名/URL 的版本一致，否则用户在
   「已安装程序」里看到的版本会对不上（发布前先 `pwsh scripts/check-release-version.ps1`
   确保源码三处版本号已同步）。

### 推荐用 wingetcreate 自动完成（会让你下载外部工具）

`wingetcreate` 会自动下载安装器、计算 SHA256、探测 InstallerType/架构/ProductCode，并校验 schema，最省心：

```powershell
winget install Microsoft.WingetCreate
# 新建（交互式补全元数据，自动算哈希）：
wingetcreate new https://dl.sj-tu.com/sjtu-monitor/v0.5.1/SJTU-Monitor_0.5.1_x64_zh-CN.msi
# 或基于本仓库现有 manifest 更新到新版本 URL：
wingetcreate update SJTU.SJTUMonitor --version 0.5.1 --urls https://dl.sj-tu.com/sjtu-monitor/v0.5.1/SJTU-Monitor_0.5.1_x64_zh-CN.msi
# 本地校验并在沙盒试装：
winget validate --manifest packaging/winget/0.5.1
winget install --manifest packaging/winget/0.5.1
# 校验通过后提交 PR（需 GitHub 授权）：
wingetcreate submit packaging/winget/0.5.1
```

手动路径也可：fork `microsoft/winget-pkgs`，把三个文件放到
`manifests/s/SJTU/SJTUMonitor/0.5.1/`，`winget validate` + 沙盒试装通过后发 PR。

### 关于签名

winget 社区仓库**不强制**代码签名，未签名的 MSI 也能提交。但未签名或自签名会触发
SmartScreen/UAC「未知发布者」提示，验证流水线偶尔也会被 Defender 拦。若要消除警告，
需公共 CA 颁发的代码签名证书（EV 立即通过 SmartScreen，OV 需累积下载信誉）。Store 用的
那张自签名证书（Subject 为 GUID，仅用于匹配 Partner Center Publisher）在 Store 外分发无效。
