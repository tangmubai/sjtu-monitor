# Microsoft Store 发布说明

本项目通过 MSIX 提交 Microsoft Store，同时保留 Tauri 生成的 MSI/NSIS 供 GitHub Release 直装。

## Store 身份（不可修改）

| 字段 | 值 |
| --- | --- |
| Identity Name | `SJTU.5685085ABA108` |
| Publisher | `CN=3D526C73-BBEC-4792-8363-1034EBA5483A` |
| Publisher display name | `SJTU` |
| Store ID | `9PPT8WJ06C0G` |

`Package Family Name` 和 `Package SID` 是安装后的运行时标识，不写入 manifest。

## 首次发布前

1. 将 Partner Center 的隐私政策 URL 设置为 `https://github.com/tangmubai/sjtu-monitor/blob/develop/docs/privacy-policy.md`。
2. 创建一个可导出的 PFX，并在 GitHub Actions Secrets 创建 `MSIX_CERTIFICATE_BASE64` 与 `MSIX_CERTIFICATE_PASSWORD`。PFX 的 Subject 必须与 manifest 中的 Publisher 完全一致。可在 PowerShell 中执行：

   ```powershell
   $password = Read-Host -AsSecureString 'PFX password'
   $cert = New-SelfSignedCertificate -Type Custom -Subject 'CN=3D526C73-BBEC-4792-8363-1034EBA5483A' -KeyUsage DigitalSignature -KeyExportPolicy Exportable -CertStoreLocation Cert:\CurrentUser\My -HashAlgorithm SHA256
   Export-PfxCertificate -Cert $cert -FilePath .\sjtu-monitor-store-signing.pfx -Password $password
   [Convert]::ToBase64String([System.IO.File]::ReadAllBytes('.\sjtu-monitor-store-signing.pfx'))
   ```

   将最后一行输出完整复制到 `MSIX_CERTIFICATE_BASE64`；将创建时输入的密码放入 `MSIX_CERTIFICATE_PASSWORD`。不要提交 PFX 文件。
3. 在 Windows SDK 安装 `MakeAppx.exe`、`SignTool.exe` 与 Windows App Certification Kit。Windows ARM64 可运行前两个工具，但 WACK 目前没有可安装的 ARM64 主组件；请在 x64 Windows 机器或 x64 Windows VM 上完成下一步。
4. 在 x64 Windows 上使用 Release MSIX 安装后，运行 `appcert.exe reset`，再执行 `appcert.exe test -appxpackagepath <MSIX路径> -reportoutputpath <XML报告路径>`。
5. 准备商店说明、支持链接、隐私政策、中文截图和审核说明。审核说明应指出：JAccount 由用户主动填写；网络同步必须由用户点击发起；应用并非上海交通大学官方产品。

## 本地打包

先构建含 sidecar 的 Tauri Release，再执行：

```powershell
pwsh -File scripts/check-release-version.ps1
pwsh -File packaging/build-msix.ps1 -Version 0.5.0.0 -CertificatePath .\store-signing.pfx -CertificatePassword '<password>'
```

只检查 MSIX 结构、尚未取得证书时，可临时追加 `-SkipSign`；该产物不能用于安装、认证或提交。GitHub 的手动工作流会生成这种结构检查包；标签发布则会强制要求签名。
