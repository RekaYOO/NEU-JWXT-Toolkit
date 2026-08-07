# 发行与 CI/CD

## 版本来源

根目录 `VERSION` 是发行版本的唯一来源。后端健康检查、Nuitka 程序和发行文件名均从该文件读取。Git 标签必须使用相同版本并带 `v` 前缀，例如 `VERSION` 为 `1.4.0` 时使用标签 `v1.4.0`。

项目定义三种运行模式：

| 模式 | 用途 | 数据目录 |
|------|------|----------|
| `development` | 仓库源码运行 | 仓库当前目录下的 `data/` |
| `desktop` | Windows 本机发行版 | `%LOCALAPPDATA%\NEU-JWXT-Toolkit\data` |
| `server` | Linux 常驻服务 | `/var/lib/neu-jwxt-toolkit` |

`NEU_JWXT_DATA_DIR` 在全部模式下具有最高优先级。发行程序通过统一资源定位函数读取打包内的 `frontend/build` 和 `VERSION`。

## 应用图标

透明母版与来源说明位于 `assets/branding/`。网页图标从母版派生到 `frontend/public/`；
Windows 多尺寸 ICO 位于 `packaging/windows/app.ico`。Nuitka 将同一 ICO 嵌入桌面 EXE
并随 standalone 载荷复制，托盘运行时优先加载该文件。无签名安装器当前不再生成。

图标改动必须同时验证 favicon、Web Manifest、16 像素托盘效果和便携包内 ICO，
不得只替换其中一个派生文件。

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
python packaging/nuitka/build.py desktop
python tools/check_release_bundle.py dist/NEU-JWXT-Toolkit
```

Linux：

```bash
sudo apt-get install build-essential patchelf ccache
python packaging/nuitka/build.py server
```

Windows 与 Linux 均使用 Nuitka `standalone`，前端构建和 `VERSION` 被复制进独立载荷
目录。Windows 只把该载荷压成普通便携 ZIP；不再用 Inno Setup 包成无签名自解压安装器。
Linux 载荷与安装脚本、systemd 单元和反代示例一起进入 `tar.gz`。

Windows 构建不启用 `onefile`、UPX 或额外可执行压缩，依赖 DLL/PYD 和资源保持外置；入口
EXE 写入稳定的 CompanyName、ProductName、FileDescription、FileVersion 与 ProductVersion
资源，并由工作流复核。这样保留约 2 秒的首次冷启动和紧凑 standalone 布局，同时减少
匿名新 PE 的特征不稳定性，但这些文本元数据不能替代 Authenticode，也不保证安全软件不告警。

1.4.5 与 1.4.6 使用了相同的旧版 PyInstaller spec；同一台 Windows 主机和同一组
Defender 安全智能下，官方 1.4.5 启动器未检出，而官方 1.4.6 启动器被启发式检测为
`Program:Win32/Wacapew.A!ml`。两版之间主要变化是业务载荷显著增加，并非 spec 切换。
这说明问题不是简单的下载来源差异，Defender 的模型、文件信誉和业务载荷都会影响判定。
后续版本改用 Nuitka standalone，以保留编译后的启动性能和可检查的外置依赖。v1.5.0
实际命中 `Program:Win32/Contebrew.A!ml` 的文件是无签名 Inno Setup 自解压安装器，因此
后续暂停安装器，只发布普通 ZIP。结论仍以同机扫描和最终发行产物复验为准，不把
“本次未检出”扩展成长期安全保证。

## 自动化

`.github/workflows/ci.yml` 在 `main` push 和 pull request 上运行。功能分支由 PR 事件验证，
不再同时为同一个提交运行 branch-push 与 PR 两套检查；合并后的 `main` 再运行一次：

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
2. 构建一次 Release 专用 React 静态资源并作为同一 workflow 的 `web-build` 传给两个
   平台任务；Release 不重复执行普通 CI 已覆盖的后端测试、前端测试和源码编译检查；
3. 分别构建 Windows x64 与 Linux amd64 standalone 程序；
4. 对两条最终产物路径分别验收：
   - 便携 ZIP 解压到新临时目录后，检查目录结构和敏感数据，再验证健康检查、首页、
     单实例、关闭浏览器后再次启动恢复页面，以及关闭；
   - Linux tar 包解压后，从解压目录启动服务，验证健康检查、首页、未授权状态和
     命令行健康检查；
5. 选择 Defender Platform 目录中的最新扫描器、更新安全智能，以禁用自动处置的方式
   扫描最终 Windows ZIP 和解压目录；禁用处置后检出会返回非零并显示在命令输出中，工作流
   同时核对扫描前后文件摘要。检测、文件变化、扫描器/服务不可用、安全智能过期或扫描失败
   都会使 Windows 任务失败，不上传该候选包；
6. 生成排序稳定的 `SHA256SUMS.txt`，为所有发行文件生成 GitHub artifact
   attestation，并上传到同一个 GitHub Release。

手动触发工作流只构建并保留 Actions 产物，不自动创建 Release；为避免同名候选包与已
发布文件混淆，`VERSION` 对应的远端标签已经存在时会拒绝手动构建，需先升级版本号。
为保持流程简单，Release 不跨 workflow 查询 CI 状态或下载历史产物；分支保护负责确保
主分支合入前通过 CI，发布者必须只从已通过 CI 的 `main` 提交创建正式标签。

上述自动化验证的是特定 GitHub runner 上的成品布局和核心启动流程，不能替代所有
Windows 版本、企业安全策略、代理配置和真实升级场景的人工验收。

## Windows 信任、Defender 与误报

项目当前无法提供可靠的 Authenticode 项目签名。发行工作流不读取 PFX、证书密码或代码
签名 Secrets，也不调用 SignTool。Windows 入口 EXE 仍未签名，但使用稳定版本资源并只在
普通 ZIP 中发布；无签名安装器暂停发布。文本版 CompanyName/ProductName 只便于识别，
不能建立 Windows 发布者信誉。

每次 Windows 构建都会附带 `WINDOWS-SECURITY-STATUS.txt`，明确记录 standalone 打包策略和本次
Defender 状态。`passed` 只表示最终 ZIP 与解压目录在当时 runner 上以禁用自动处置方式
扫描、MpCmdRun 返回 0 且文件摘要未变化。扫描器不可用不会降级发布；校验和、GitHub
构建来源证明和安全软件检测分别回答不同问题，不能互相替代，也不作长期“无病毒”声明。

Defender 检测记录包括 `WINDOWS-SECURITY-STATUS.txt` 中的结果，以及 Release Actions
中“Scan final Windows artifacts with Microsoft Defender”步骤的日志和结论。后者还记录
引擎与安全智能版本。它只是当时 runner 上一个引擎版本的扫描结果；某次扫描为 `passed`，
不能扩展解释为所有环境、所有时间均无风险。

收到 Defender/SmartScreen 告警时：

1. 不要关闭实时防护、添加整个目录到排除项或绕过组织安全策略；
2. 停止运行文件，记录发行版本、下载地址、文件 SHA-256、检测名称、安全智能版本和
   告警截图；
3. 对照 `SHA256SUMS.txt`，并验证 GitHub artifact attestation；摘要或来源验证失败时
   删除文件并报告项目维护者；
4. 摘要和来源均正确但仍被 Defender 检测时，由维护者或受影响用户通过
   [Microsoft Security Intelligence 样本提交入口](https://www.microsoft.com/en-us/wdsi/filesubmission)
   申报疑似误报，并保留提交编号；
5. 在 Microsoft 给出结果或项目发布新版本前，保持隔离，不把“CI 扫描通过”当作绕过
   单位安全策略或强行运行的理由。

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

Linux tar 包使用相同命令替换文件名即可。Attestation 验证的是产物摘要与
GitHub Actions 构建身份的关联，不审计源码逻辑、不提供 Authenticode 发布者身份，也不是
恶意软件扫描结果。摘要匹配只能说明下载文件与发布清单一致；若发布源本身不可信，校验和
不能单独建立信任。

## 发行前检查

- Git 标签版本必须与 `VERSION` 完全一致。
- 发行包不得包含仓库 `data/`、`.env`、日志、真实凭据、会话或开发依赖。
- Windows 自动化必须验收解压后的便携包、PE 版本资源和真实启动链；正式发布仍应在
  未安装 Python/Node.js 的干净 Windows 10/11 环境，从浏览器下载后抽查保留、解压和启动。
- Linux 应验证安装、重启、升级成功、健康检查失败回滚和两种反向代理。
- 检查 Actions 中 Windows standalone 打包声明和 Defender 步骤的实际结论；扫描器、
  服务或新鲜安全智能不可用时必须阻止 Windows 产物发布。
- 下载 Release 成品后复核 `SHA256SUMS.txt` 和 artifact attestation，不能只校验
  Actions 中间产物。
- 正式模式只绑定 `127.0.0.1`，不启用跨域白名单以外的访问，也不公开 Swagger/OpenAPI。
