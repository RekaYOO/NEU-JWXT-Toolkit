# Windows 本地安装

Windows 发行版用于单台电脑上的本机使用。它不需要 Python、Node.js 或管理员权限，也不会向局域网开放端口。

项目目前无法提供可靠的 Authenticode 项目签名。v1.5.0 的无签名自解压安装器已被
Defender 启发式检测，因此 Windows 正式发行物只保留编译后的 standalone 便携 ZIP，
`setup.exe` 暂停发布。请只使用本项目 GitHub Releases 的文件，并在运行前核对校验和
与 GitHub 构建来源；项目不会要求用户关闭 Defender 或添加安全排除项。

## 便携版

下载 `NEU-JWXT-Toolkit-<版本>-windows-x64-portable.zip`，完整解压后运行
`NEU-JWXT-Toolkit.exe`。不要直接在压缩包内运行，也不要只复制 EXE；同目录的 DLL、
PYD、前端资源和版本文件都是运行所必需的。

便携版采用 Nuitka `standalone` 编译目录并附带固定依赖和已经构建完成的前端资源。用户
不需要安装 Python、pip、Node.js，也不会在首次启动时联网安装依赖。构建不使用 onefile、
UPX 或额外可执行压缩，并为入口写入稳定的产品名、发行者文本和版本资源。这保留冷启动
性能与紧凑目录，但未签名 EXE 仍可能被 Defender 或 SmartScreen 告警。

“便携”只表示程序无需安装。出于升级安全和避免误删，用户数据仍统一保存到：

```text
%LOCALAPPDATA%\NEU-JWXT-Toolkit\data
```

## 下载验证与安全软件告警

建议同时从同一 GitHub Release 下载 `SHA256SUMS.txt`。例如验证便携包：

```powershell
Get-FileHash -Algorithm SHA256 .\NEU-JWXT-Toolkit-<版本>-windows-x64-portable.zip
Get-Content .\SHA256SUMS.txt
```

两处同名文件的 SHA-256 必须一致。安装 GitHub CLI 后，还可验证 GitHub Actions 构建
来源：

```powershell
gh attestation verify .\NEU-JWXT-Toolkit-<版本>-windows-x64-portable.zip `
  --repo RekaYOO/NEU-JWXT-Toolkit
```

发行物还包含 `WINDOWS-SECURITY-STATUS.txt`，记录 compiled standalone 打包策略、
安装器暂停状态以及 Defender 验证状态。扫描会先更新安全智能并确认服务正常，随后禁用
自动处置、保留完整命令输出并核对扫描前后文件摘要；扫描器、服务或新鲜安全智能不可用时，
Windows 发行任务会直接失败，不会上传该候选包。即使门禁通过，文件也不会声称“无病毒”。
SHA-256、GitHub 来源证明和 Defender 检测含义不同；任何一项都不能单独证明软件绝对安全。

如果 Defender 或 SmartScreen 告警，不要关闭实时防护，也不要添加整个目录到排除项。
先停止运行并记录版本、下载地址、SHA-256、检测名称和截图，再核对校验和与 GitHub
来源证明。二者验证失败时删除文件并联系项目维护者；二者均正确但 Defender 仍检测时，
保持文件隔离，并通过
[Microsoft Security Intelligence 样本提交入口](https://www.microsoft.com/en-us/wdsi/filesubmission)
申报疑似误报。
不要因为 CI 曾扫描通过就绕过单位安全策略或强行运行。

## 启动与退出

- 服务启动后会在 Windows 通知区域（通常收纳在“隐藏的图标”中）显示托盘图标。
- 托盘使用项目应用图标，便于确认正在运行的是本工具。
- 双击托盘图标、右键选择“打开教务工具箱”，或再次运行 `NEU-JWXT-Toolkit.exe`，都会重新打开已有
  页面，不会创建第二个服务实例。
- 关闭浏览器标签页不会结束本地服务。要完整退出，右键托盘图标选择“退出程序”，或
  点击页面右上角的电源按钮；用户菜单中也保留同一入口。
- Windows 注销或关机时，系统也会结束服务。

程序始终绑定 `127.0.0.1`，不提供局域网服务模式。若需要在手机等其他设备访问，请在 Linux 服务器上安装服务版。

## 故障排查

- 页面没有自动打开：双击托盘图标或再次运行 `NEU-JWXT-Toolkit.exe`，并检查安全软件是否拦截了程序
  的本地监听。
- 安全软件报告威胁：不要禁用防护；按“下载验证与安全软件告警”保留信息并核对来源。
- 升级后数据缺失：确认没有设置调试变量 `NEU_JWXT_DATA_DIR`，并检查上述数据目录。
- 想彻底清除程序和数据：先退出程序，删除解压目录，再手动删除上述数据目录。

