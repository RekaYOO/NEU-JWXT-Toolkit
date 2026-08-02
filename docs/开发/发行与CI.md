# 发行与 CI/CD

## 版本来源

根目录 `VERSION` 是发行版本的唯一来源。后端健康检查、PyInstaller 程序和发行文件名均从该文件读取。Git 标签必须使用相同版本并带 `v` 前缀，例如 `VERSION` 为 `1.4.0` 时使用标签 `v1.4.0`。

项目定义三种运行模式：

| 模式 | 用途 | 数据目录 |
|------|------|----------|
| `development` | 仓库源码运行 | 仓库当前目录下的 `data/` |
| `desktop` | Windows 本机发行版 | `%LOCALAPPDATA%\NEU-JWXT-Toolkit\data` |
| `server` | Linux 常驻服务 | `/var/lib/neu-jwxt-toolkit` |

`NEU_JWXT_DATA_DIR` 在全部模式下具有最高优先级。冻结程序通过统一资源定位函数读取打包内的 `frontend/build` 和 `VERSION`。

## 本地构建

发行环境固定使用 Python 3.11 和 Node.js 20：

```bash
cd frontend
npm ci
npm run build
cd ..
python -m pip install -r requirements-build.lock
```

Windows：

```powershell
pyinstaller packaging/pyinstaller/desktop.spec --clean --noconfirm
$version = (Get-Content VERSION -Raw).Trim()
$translation = Join-Path $env:TEMP "ChineseSimplified.isl"
Invoke-WebRequest `
  "https://raw.githubusercontent.com/kira-96/Inno-Setup-Chinese-Simplified-Translation/6da09d23e14443d4cf8f07b1c5fd821bfe459788/ChineseSimplified.isl" `
  -OutFile $translation
if ((Get-FileHash -Algorithm SHA256 $translation).Hash.ToLowerInvariant() -ne `
  "869e43e7c7b8d20c7e4397c8e98f7d1b7cf0528803acdf019ad350143ec85469") {
  throw "ChineseSimplified.isl 校验失败"
}
ISCC.exe `
  "/DMyAppVersion=$version" `
  "/DSourceDir=$pwd\dist\NEU-JWXT-Toolkit" `
  "/DChineseMessagesFile=$translation" `
  packaging\windows\installer.iss
```

Linux：

```bash
pyinstaller packaging/pyinstaller/server.spec --clean --noconfirm
```

PyInstaller 使用 `onedir`，前端构建被复制进冻结目录。Windows 的同一 `onedir`
既用于便携 ZIP，也由 Inno Setup 生成按用户安装器；Linux 的 `onedir` 与安装脚本、
systemd 单元和反代示例一起进入 `tar.gz`。

Windows 冻结载荷采用以下可审计打包策略：

- 关闭 UPX，不对可执行文件和依赖做额外可执行压缩；
- 启用 PyInstaller `noarchive`，将 Python 字节码作为 `_internal` 下的外置模块，
  而不是集中进单个 PYZ；
- 保持 `onedir` 目录布局，启动器之外的运行库、前端和版本文件均可单独检查。

这些选择用于提高产物的可检查性、故障定位能力和构建透明度，不是规避杀毒软件，
也不会改变程序行为或保证安全软件不告警。Windows 安装版复用同一冻结载荷，因此同样
具备这些属性。Linux 服务版仍为 `onedir` 且关闭 UPX，但保留 PYZ，以控制服务包的
文件数量；其审计边界是源码、最终 tar 包、校验和与构建来源证明。

1.4.5 与 1.4.6 使用了相同的旧版 PyInstaller spec；同一台 Windows 主机和同一组
Defender 安全智能下，官方 1.4.5 启动器未检出，而官方 1.4.6 启动器被启发式检测为
`Program:Win32/Wacapew.A!ml`。两版之间主要变化是业务载荷显著增加，并非 spec 切换。
这说明问题不是简单的下载来源差异，也不能仅用“未签名”解释；旧布局把全部模块集中进
较大的不透明归档，使无害业务变化也可能跨过机器学习检测阈值。当前外置模块策略用于
消除这种脆弱的聚合形态。结论以同机扫描和最终发行产物复验为准，不把“本次未检出”
扩展成长期安全保证。

## 自动化

`.github/workflows/ci.yml` 在 push 和 pull request 上运行：

- 后端测试；
- 前端交互与业务规则测试；
- React 生产构建；
- Python 编译检查；
- 静态首页挂载测试。

CI 默认权限限制为 `contents: read`，所用 GitHub Actions 均固定到经过核对的完整提交
SHA，避免浮动主版本标签在未审阅时改变执行内容。升级 Action 时应同时更新注释中的
可读版本号和 `uses` 的提交 SHA，并通过正常评审验证来源。

`.github/workflows/release.yml` 在推送 `v*` 标签时：

1. 校验 `VERSION` 格式以及标签与版本的一致性；
2. 只构建一次 React 静态资源并在任务间传递；
3. 分别构建 Windows x64 与 Linux amd64 冻结程序；
4. 对三条最终产物路径分别验收：
   - 便携 ZIP 解压到新临时目录后，检查目录结构和敏感数据，再验证健康检查、首页、
     单实例、关闭浏览器后再次启动恢复页面，以及关闭；
   - 安装器静默安装到临时目录后，检查安装目录并启动验证，再静默卸载并确认启动器
     已移除；
   - Linux tar 包解压后，从解压目录启动服务，验证健康检查、首页、未授权状态和
     命令行健康检查；
5. 在 runner 提供 Microsoft Defender 命令行扫描器时，扫描最终 Windows ZIP、
   安装器、解压目录和安装后的真实目录；检测或扫描失败会使任务失败，扫描器不可用则
   在 Actions 中记录 notice，但不跳过成品冒烟测试；
6. 生成排序稳定的 `SHA256SUMS.txt`，为所有发行文件生成 GitHub artifact
   attestation，并上传到同一个 GitHub Release。

手动触发工作流只构建并保留 Actions 产物，不自动创建 Release；为避免同名候选包与已
发布文件混淆，`VERSION` 对应的远端标签已经存在时会拒绝手动构建，需先升级版本号。

上述自动化验证的是特定 GitHub runner 上的成品布局和核心启动流程，不能替代所有
Windows 版本、企业安全策略、代理配置和真实升级场景的人工验收。

## Windows 信任、Defender 与误报

Windows Authenticode 签名是可选的发行增强项。仓库同时配置
`WINDOWS_SIGNING_CERTIFICATE_BASE64` 和 `WINDOWS_SIGNING_CERTIFICATE_PASSWORD`
时，工作流会签名并验证便携版启动器和最终安装器，并使用时间戳服务；两项都未配置时，
发行继续生成明确标记为 unsigned 的产物；只配置其中一项会直接失败。每次 Windows
构建都会附带 `WINDOWS-SIGNING-STATUS.txt`，不要根据文件名或 Release 页面猜测签名
状态。该文件也明确记录本次 Defender 状态：`passed` 表示最终 ZIP、安装器和解压目录
在当时的 runner 上完成扫描且未检出；`unavailable` 表示扫描器不可用，并明确不作
“无病毒”声明。

签名只能辅助确认发布者及文件签名后的完整性，并可能改善系统信誉；它不证明程序没有
恶意行为，也不能保证 Defender 或 SmartScreen 不告警。反过来，未签名本身也不能
证明文件有害。校验和、GitHub 构建来源证明、签名和安全软件检测分别回答不同问题，
不能互相替代。

Defender 检测记录包括 `WINDOWS-SIGNING-STATUS.txt` 中的结果，以及 Release Actions
中“Scan final Windows artifacts with Microsoft Defender when available”步骤的日志
和结论。后者在可用时还记录引擎与安全智能版本。它只是当时 runner 上一个引擎版本的
扫描结果；状态为 `unavailable`、或某次扫描为 `passed`，都不能扩展解释为所有环境、
所有时间均无风险。

收到 Defender/SmartScreen 告警时：

1. 不要关闭实时防护、添加整个目录到排除项或绕过组织安全策略；
2. 停止运行文件，记录发行版本、下载地址、文件 SHA-256、检测名称、安全智能版本和
   告警截图；
3. 对照 `SHA256SUMS.txt`，并验证 GitHub artifact attestation；摘要或来源验证失败时
   删除文件并报告项目维护者；
4. 摘要和来源均正确但仍被 Defender 检测时，由维护者或受影响用户通过
   [Microsoft Security Intelligence 样本提交入口](https://www.microsoft.com/en-us/wdsi/filesubmission)
   申报疑似误报，并保留提交编号；
5. 在 Microsoft 给出结果或项目发布新版本前，保持隔离，不把“CI 扫描通过”或“已有
   签名”当作强行运行的理由。

## 校验下载文件

从 GitHub Release 下载目标文件和同版本的 `SHA256SUMS.txt`。在 PowerShell 中查看
文件摘要：

```powershell
Get-FileHash -Algorithm SHA256 .\NEU-JWXT-Toolkit-<版本>-windows-x64-portable.zip
Get-Content .\SHA256SUMS.txt
```

`Get-FileHash` 输出必须与 `SHA256SUMS.txt` 中同名文件的值逐字一致。在 Linux 上，
下载该 Release 的全部文件后可执行：

```bash
sha256sum --check SHA256SUMS.txt
```

安装 GitHub CLI 后，还可以验证文件是否由本仓库的 GitHub Actions 工作流产生：

```bash
gh attestation verify NEU-JWXT-Toolkit-<版本>-windows-x64-portable.zip \
  --repo RekaYOO/NEU-JWXT-Toolkit
```

安装器和 Linux tar 包使用相同命令替换文件名即可。Attestation 验证的是产物摘要与
GitHub Actions 构建身份的关联，不审计源码逻辑、不等同于 Authenticode 发布者签名，
也不是恶意软件扫描结果。摘要匹配只能说明下载文件与发布清单一致；若发布源本身不可信，
校验和不能单独建立信任。

## 发行前检查

- Git 标签版本必须与 `VERSION` 完全一致。
- 发行包不得包含仓库 `data/`、`.env`、日志、真实凭据、会话或开发依赖。
- Windows 自动化必须验收解压后的便携包，以及安装、启动、卸载后的安装版；正式发布
  仍应在未安装 Python/Node.js 的干净环境抽查两条路径。
- Linux 应验证安装、重启、升级成功、健康检查失败回滚和两种反向代理。
- 检查 Actions 中 Windows 签名状态和 Defender 步骤的实际结论；扫描器不可用必须
  作为发行记录保留，不能写成“扫描通过”。
- 下载 Release 成品后复核 `SHA256SUMS.txt` 和 artifact attestation，不能只校验
  Actions 中间产物。
- 正式模式只绑定 `127.0.0.1`，不启用跨域白名单以外的访问，也不公开 Swagger/OpenAPI。
