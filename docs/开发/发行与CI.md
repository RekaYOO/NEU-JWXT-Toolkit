# 发行与 CI/CD

正式 Windows 和 Linux 发行包必须同时包含根目录 `LICENSE`，以及位于
`backend/core/course_selection/THIRD_PARTY_NOTICE.md` 的选课策略模型上游许可说明。
`tools/check_release_bundle.py` 会把两者作为发行结构的一部分校验。

## 版本来源

根目录 `VERSION` 是面向用户的发行版本唯一来源。后端健康检查、Nuitka 程序、Android
`versionName` 和发行文件名均从该文件读取。Android 另用正整数 `ANDROID_VERSION_CODE` 作为
系统覆盖安装顺序，每次 Android 正式发布都必须递增。Git 标签必须与 `VERSION` 相同并带 `v` 前缀。

项目定义四种运行模式：

| 模式 | 用途 | 数据目录 |
|------|------|----------|
| `development` | 仓库源码运行 | 仓库当前目录下的 `data/` |
| `desktop` | Windows 本机发行版 | `%LOCALAPPDATA%\NEU-JWXT-Toolkit\data` |
| `server` | Linux 常驻服务 | `/var/lib/neu-jwxt-toolkit` |
| `mobile` | Android 本地版内嵌服务 | Android 应用内部目录 |

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
npm run test:performance
cd ..
python -m pip install -r requirements-build.lock
```

React 继续构建为一个主包。`npm run build` 随后运行 `tools/prepare_frontend_assets.mjs`，为 JS、
CSS、HTML、JSON、SVG 等文本资源生成确定性的 gzip/Brotli 变体，并删除生产目录中的 source
map。FastAPI 内置内容协商和缓存头，因此本地单端口、Windows、Linux、1Panel 均无需额外配置
Nginx/Caddy 才能获得压缩传输。带哈希资源使用一年 immutable 缓存；HTML 和根级资源重新验证。

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

Android 调试包需要 JDK 17、Android SDK 35 和 Python 3.13。先构建前端，再为目标 ABI 构建
锁定的 Android wheel，最后运行 Gradle Wrapper：

```bash
bash packaging/android/build_android_wheels.sh x86_64
cd packaging/android
bash gradlew :client-app:assembleX86TestDebug :local-app:assembleX86TestDebug
```

`packaging/android/` 是 `shared`、`client-app`、`local-app` 三模块工程。客户端模块不应用
Chaquopy 插件；本地模块使用 Chaquopy 17、Python 3.13，并只从 `wheelhouse` 安装锁定依赖。
`pydantic-core`、`lxml` 和 `pycryptodome` 通过 cibuildwheel 的 Android/NDK 交叉编译支持生成，
不使用 Termux wheel。正式包只构建 `arm64-v8a`，普通 Android CI 构建 `x86_64` 调试包。

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
- 单主包、gzip/Brotli 体积预算和运行目录无 source map 检查；
- Python 编译检查；
- 静态首页挂载测试。

同一 PR 连续推送时取消已被替代的旧 CI，最新提交仍执行所有门禁；`main` push 和手动
运行使用各自独立的并发组，不相互取消，也不取消 Release 工作流。
`.github/workflows/android.yml` 另使用 Python 3.13 跑完整后端测试，构建两个 `x86_64` 调试
APK，并在 API 35 模拟器完成安装、启动和本地页面 smoke test。调试包只用标准 debug 签名。
前端全量测试使用最多两个 worker，同时检查增量测试选择器自身的回归测试。
本地 `test:changed`/`test:related` 只用于缩短开发反馈，不用于替换 CI 全量、生产构建、
体积预算、同源检查和成品 smoke test。不得因增量命令没有选中用例就报告完整回归通过。

当前单主包预算为 gzip 612 KiB、Brotli 500 KiB。gzip 上限在共享课表网格、手机堆叠缩略视图和
内置多校区作息发布后重新基准化，当前 CI 产物约 607 KiB，并保留有限余量；该预算检查的是
`prepare_frontend_assets.mjs` 生成的实际预压缩传输文件，不是 CRA 根据未二次压缩产物显示的
通用体积提醒。新增完整业务能力后如确需调整预算，必须记录原因并保留小幅余量，不能仅为让
CI 变绿而无限抬高上限。

CI 默认权限限制为 `contents: read`，所用 GitHub Actions 均固定到经过核对的完整提交
SHA，避免浮动主版本标签在未审阅时改变执行内容。升级 Action 时应同时更新注释中的
可读版本号和 `uses` 的提交 SHA，并通过正常评审验证来源。

`.github/workflows/release.yml` 在推送 `v*` 标签时：

1. 校验 `VERSION` 格式以及标签与版本的一致性；
2. 构建一次 Release 专用 React 静态资源；source map 只上传为保留 30 天的私有 Actions
   artifact，随后从运行目录删除，再将同一个 `web-build` 传给两个平台任务；Release 不重复
   执行普通 CI 已覆盖的后端测试、前端测试和源码编译检查；
3. 分别构建 Windows x64、Linux amd64、Android 客户端 arm64 和 Android 本地版 arm64；
4. 对两条最终产物路径分别验收：
   - 便携 ZIP 解压到新临时目录后，检查目录结构和敏感数据，再验证健康检查、首页、
     单实例、关闭浏览器后再次启动恢复页面，以及关闭；
   - Linux tar 包解压后，从解压目录启动服务，验证健康检查、首页、未授权状态和
     命令行健康检查；
5. Android 构建必须读取固定签名 Secrets，并用 `apksigner`、`aapt` 和 ZIP 内容检查验证包名、
   版本、API 24、单 ABI、相同签名、客户端不含 Python、本地版包含 Python、无 source map 和
     敏感配置；两个 APK 任一个失败都会阻止整个 Release；
6. 生成 Windows 安全状态说明，明确未签名、未发布安装器且自动杀毒扫描不作为发行门禁；
7. 生成排序稳定的 `SHA256SUMS.txt`，为所有发行文件生成 GitHub artifact
   attestation，并上传到同一个 GitHub Release。

手动触发工作流只构建并保留 Actions 产物，不自动创建 Release；为避免同名候选包与已
发布文件混淆，`VERSION` 对应的远端标签已经存在时会拒绝手动构建，需先升级版本号。
为保持流程简单，Release 不跨 workflow 查询 CI 状态或下载历史产物；分支保护负责确保
主分支合入前通过 CI，发布者必须只从已通过 CI 的 `main` 提交创建正式标签。

### Android 固定签名与候选构建

仓库 Actions Secrets 使用以下四个名称，两个 Android 正式包共用同一签名：
`ANDROID_SIGNING_KEYSTORE_BASE64`、`ANDROID_SIGNING_STORE_PASSWORD`、
`ANDROID_SIGNING_KEY_ALIAS` 和 `ANDROID_SIGNING_KEY_PASSWORD`。Keystore 的 Base64
不是加密，不得写入仓库、日志或构建产物。初始化前先核对已有 Secrets；不得通过重新生成
密钥修复配置错误，否则现有正式安装将无法覆盖升级。

维护者必须在仓库外保留 Keystore、密码、别名和公开证书的完整备份，并另存于加密离线
存储。GitHub Secrets 无法作为可下载恢复的密钥备份。可通过 GitHub Secrets API 的
公开密钥和标准 sealed-box 加密一次配置四项，无需修改工作流或为每次构建生成新密码。

候选构建先递增 `VERSION` 和 `ANDROID_VERSION_CODE`，再手动运行 `Release` 工作流。
全部成功后，`android-release` artifact 包含两个固定签名、正式包名的 APK；
Windows/Linux 成品分别位于对应的 release artifact。候选 artifact 默认保留 7 天，
它不是已公开发布的 GitHub Release，也尚未经过标签发布阶段的统一摘要与来源证明。
正式标签只应指向通过 CI 和必要真机验收的 `main` 提交。

调试 APK 使用 `.debug` 包名和 Runner 调试密钥，与正式包独立安装、独立存储；不能假设
安装正式包会自动迁移调试包的数据，也不要为解决签名冲突而让用户卸载旧包丢失本地数据。

通知端到端测试用与通知相同的显式 Activity Intent 启动 `ActivityScenario`；桌面启动入口另由
缓存冷启动测试覆盖。`ActivityScenario` 按 Intent 的 action/categories 等字段跟踪生命周期，
通知更新 Intent 后再手动改回旧值不能补回已丢弃的生命周期事件。测试必须先离开登录页地址，
再验证真实通知重新打开登录页，并确认 Activity 能暂停、恢复和销毁；不得跳过销毁断言或
仅延长超时来掩盖跟踪失配。此约束仅属于测试，不更改应用正常的通知 Intent 或跳转行为。

上述自动化验证的是特定 GitHub runner 上的成品布局和核心启动流程，不能替代所有
Windows 版本、企业安全策略、代理配置和真实升级场景的人工验收。

Android 正式签名固定使用以下 GitHub Secrets：

- `ANDROID_SIGNING_KEYSTORE_BASE64`
- `ANDROID_SIGNING_STORE_PASSWORD`
- `ANDROID_SIGNING_KEY_ALIAS`
- `ANDROID_SIGNING_KEY_PASSWORD`

标签和手动 Release 候选构建缺少任一项时直接失败，不生成临时签名。正式文件名固定为
`NEU-JWXT-Toolkit-<version>-android-client-arm64.apk` 与
`NEU-JWXT-Toolkit-<version>-android-local-arm64.apk`，并与桌面产物一起进入 SHA-256 清单和
GitHub artifact attestation。

Android 双 ABI 构建和 API 35 模拟器功能门禁已通过，仍待真机及正式签名混淆包验收。
`release.yml` 通过可复用 `android.yml` 先执行 x86_64 仪器测试，再构建签名 arm64 产物；
复用前端作业已通过体积预算的 `web-build`，在 Android 门禁中测试同一提交的前端代码，
不重复构建或使用另一份前端资源。
任何 Android 构建、依赖导入或模拟器门禁失败都会阻止 Windows/Linux/Android 整个 Release。
具体已验证项及剩余阻塞见 [Android 安装与运行](../部署/Android安装.md#验证状态)。

## Windows 信任、Defender 与误报

项目当前无法提供可靠的 Authenticode 项目签名。发行工作流不读取 PFX、证书密码或代码
签名 Secrets，也不调用 SignTool。Windows 入口 EXE 仍未签名，但使用稳定版本资源并只在
普通 ZIP 中发布；无签名安装器暂停发布。文本版 CompanyName/ProductName 只便于识别，
不能建立 Windows 发布者信誉。

每次 Windows 构建都会附带 `WINDOWS-SECURITY-STATUS.txt`，明确记录 standalone 打包策略、
未签名状态和安全验证边界。自动杀毒扫描不再作为发行门禁：GitHub 托管 runner 上的云端
启发式结果可能随安全智能和文件信誉变化，同一份可信源码的构建也可能得到不稳定结论。
发行流程继续验证程序结构、敏感文件排除、真实启动链、SHA-256 和 GitHub artifact
attestation；这些验证与终端安全软件检测回答不同问题，不能互相替代，也不作“无病毒”声明。

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
- 发行包不得包含 `frontend/build` 下的 `.map`；静态 JS/CSS 必须带预压缩变体并通过体积预算。
- Windows 自动化必须验收解压后的便携包、PE 版本资源和真实启动链；正式发布仍应在
  未安装 Python/Node.js 的干净 Windows 10/11 环境，从浏览器下载后抽查保留、解压和启动。
- Linux 应验证安装、重启、升级成功、健康检查失败回滚和两种反向代理。
- Android 应验证两个包名、`VERSION`/`ANDROID_VERSION_CODE`、arm64 单 ABI、固定签名、
  客户端服务器切换和 Cookie 隔离、本地登录、下载、后台任务、通知深链、进程/手机重启以及
  关闭全部任务后退出前台服务。正式发布前仍需 arm64 真机覆盖安装验收。
- 检查 Actions 中 Windows standalone 构建、便携包启动验收、敏感文件排除、校验和与
  artifact attestation 的实际结论。
- 下载 Release 成品后复核 `SHA256SUMS.txt` 和 artifact attestation，不能只校验
  Actions 中间产物。
- 正式模式只绑定 `127.0.0.1`，不启用跨域白名单以外的访问，也不公开 Swagger/OpenAPI。
