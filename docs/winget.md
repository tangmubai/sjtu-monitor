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

### 定稿前必须填的两处

发布 MSI 之前无法确定，需在 GitHub Release 出现 MSI 后补齐 `installer.yaml`：

1. **InstallerUrl**：核对实际的 MSI 资产名。Tauri（WiX）默认命名为
   `{productName}_{version}_{arch}_{wixLanguage}.msi`，当前配置下应为
   `SJTU-Monitor_0.5.1_x64_zh-CN.msi`。
2. **InstallerSha256**：
   ```powershell
   (Get-FileHash .\SJTU-Monitor_0.5.1_x64_zh-CN.msi -Algorithm SHA256).Hash
   ```
3. **ProductCode（可选但推荐，利于升级检测）**：从构建出的 MSI 读取
   ```powershell
   $installer = New-Object -ComObject WindowsInstaller.Installer
   $db = $installer.OpenDatabase("SJTU-Monitor_0.5.1_x64_zh-CN.msi", 0)
   $view = $db.OpenView("SELECT Value FROM Property WHERE Property='ProductCode'")
   $view.Execute(); $rec = $view.Fetch(); $rec.StringData(1)
   ```

### 推荐用 wingetcreate 自动完成（会让你下载外部工具）

`wingetcreate` 会自动下载安装器、计算 SHA256、探测 InstallerType/架构/ProductCode，并校验 schema，最省心：

```powershell
winget install Microsoft.WingetCreate
# 新建（交互式补全元数据，自动算哈希）：
wingetcreate new https://github.com/tangmubai/sjtu-monitor/releases/download/v0.5.1/SJTU-Monitor_0.5.1_x64_zh-CN.msi
# 或基于本仓库现有 manifest 更新到新版本 URL：
wingetcreate update SJTU.SJTUMonitor --version 0.5.1 --urls <MSI_URL>
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
