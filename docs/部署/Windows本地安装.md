# Windows 本地安装

Windows 发行版用于单台电脑上的本机使用。它不需要 Python、Node.js 或管理员权限，也不会向局域网开放端口。

Windows 发行版当前不使用 Authenticode 代码签名。因此首次运行安装器或便携版 EXE 时，
SmartScreen 可能显示“Windows 已保护你的电脑”并要求手动确认，单位管理策略也可能直接
禁止运行未签名程序。请只使用本项目 GitHub Releases 的文件，并在运行前核对校验和与
GitHub 构建来源；项目不会要求用户关闭 Defender 或添加安全排除项。

## 安装版

1. 从 GitHub Releases 下载 `NEU-JWXT-Toolkit-<版本>-windows-x64-setup.exe`。
2. 运行安装器。程序默认安装到当前用户的 `%LOCALAPPDATA%\Programs\NEU-JWXT-Toolkit`。
3. 从开始菜单启动“NEU 教务工具箱”。安装时可选择创建桌面快捷方式。
4. 程序会选择一个空闲的本机端口，健康检查通过后自动打开默认浏览器。

安装新版会完整替换 `runtime` 程序目录，避免旧模块残留；从 1.4.8 及更早版本升级时
还会清理旧 `_internal` 载荷。用户数据位于独立目录，不会被删除。

## 便携版

下载 `NEU-JWXT-Toolkit-<版本>-windows-x64-portable.zip`，完整解压后运行
`NEU-JWXT-Toolkit.exe`。不要只从压缩包内直接运行单个 EXE；同目录的 DLL、PYD、
前端资源和版本文件也是运行所必需的。

便携版采用 Nuitka `standalone` 目录：入口程序、编译后的模块、依赖库和前端资源均可
分别检查，不使用 onefile 或额外可执行压缩。这样设计是为了便于审计和排查，不是规避
安全软件，也不代表 Defender
或 SmartScreen 一定不会告警。

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

安装器可用相同命令替换文件名。发行物还包含 `WINDOWS-SECURITY-STATUS.txt`，明确记录
Windows 文件未签名，并说明 Defender 扫描是 `passed` 还是 `unavailable`。
`unavailable` 表示托管 runner 无法执行该次扫描，不代表扫描通过，文件也不会声称
“无病毒”。SHA-256、GitHub 来源证明和 Defender 检测含义不同；任何一项都不能单独
证明软件绝对安全。

如果 Defender 或 SmartScreen 告警，不要关闭实时防护，也不要添加整个目录到排除项。
先停止运行并记录版本、下载地址、SHA-256、检测名称和截图，再核对校验和与 GitHub
来源证明。二者验证失败时删除文件并联系项目维护者；二者均正确但 Defender 仍检测时，
保持文件隔离，并通过
[Microsoft Security Intelligence 样本提交入口](https://www.microsoft.com/en-us/wdsi/filesubmission)
申报疑似误报。
不要因为 CI 曾扫描通过就绕过单位安全策略或强行运行。

## 启动与退出

- 服务启动后会在 Windows 通知区域（通常收纳在“隐藏的图标”中）显示托盘图标。
- 托盘、开始菜单、桌面快捷方式和卸载列表使用同一应用图标，便于确认正在运行的是本工具。
- 双击托盘图标、右键选择“打开教务工具箱”，或再次点击快捷方式，都会重新打开已有
  页面，不会创建第二个服务实例。
- 关闭浏览器标签页不会结束本地服务。要完整退出，右键托盘图标选择“退出程序”，或
  点击页面右上角的电源按钮；用户菜单中也保留同一入口。
- Windows 注销或关机时，系统也会结束服务。

程序始终绑定 `127.0.0.1`，不提供局域网服务模式。若需要在手机等其他设备访问，请在 Linux 服务器上安装服务版。

## 故障排查

- 页面没有自动打开：双击托盘图标或再次点击快捷方式，并检查安全软件是否拦截了程序
  的本地监听。
- 安全软件报告威胁：不要禁用防护；按“下载验证与安全软件告警”保留信息并核对来源。
- 升级后数据缺失：确认没有设置调试变量 `NEU_JWXT_DATA_DIR`，并检查上述数据目录。
- 想彻底清除数据：先退出程序，再卸载程序并手动删除数据目录。

