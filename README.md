<p align="center">
  <img src="assets/branding/app-icon.png" width="112" alt="NEU 教务系统工具箱图标">
</p>

# NEU 教务系统工具箱

面向东北大学教务场景的第三方工具箱。项目把成绩、培养计划、课表、考试、课程大纲、
选课、评教、科研训练和资料导出集中到一个响应式 Web 界面，并通过本地缓存改善官方系统
响应慢或暂时不可用时的使用体验。当前稳定版本及安装包见
[GitHub Releases](https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest)。

项目支持 Windows 本机应用、Linux 单用户轻量服务、Android 客户端/本地版和源码开发。它不是东北大学
官方产品，也不会绕过学校的身份认证、选课资格或业务规则。

## 主要功能

| 功能 | 能力摘要 |
|---|---|
| 成绩明细与 GPA 模拟 | 多学期筛选、分项成绩、列设置、按入学年份默认选择且可配置的绩点策略、可保存的学分/绩点模拟方案；策略同步成绩追踪通知 |
| 培养计划 | 类别层级、课程状态、学分缺口、课程大纲联动和选课数据协同 |
| 成绩追踪 | 定时检查成绩变更并通过系统邮件通知；WebVPN 失效时调用独立的一次性认证恢复服务 |
| 查询课表 | 我的、班级、教师和教室课表，周/学期视图及统一冲突检测；四类课表共享手机缩略网格，个人课表支持浏览器快照首屏和后台静默核验 |
| 课程大纲 | 条件检索、分段详情、附件、HTML 导出，以及考核方式和成绩分制的缓存补全与失败重试 |
| 选课系统 | 抢选/权重双模式工作台、课程归档、方案组、课表预览、冲突检查、自动抢课/空位换课与策略投权 |
| 自动评教 | 读取官方评教任务与指标，支持单课确认、评分策略和逐门批量提交 |
| 活动证明快速导出 | 汇总创意节、科普节、科技节、创业节参与记录，快速归档可用证明图片 |
| 其他教务功能 | 实验选课、考试与 ICS、科研训练、学籍证明打印和资料导出 |
| 离线查看 | 在未登录时读取当前设备已保存且允许离线展示的数据 |

功能入口以代码中的
[`frontend/src/features/featureRegistry.js`](frontend/src/features/featureRegistry.js)
为准。完整使用说明见[文档中心](docs/README.md)。

## 安装与运行

### Windows 便携版

从 [GitHub Releases](https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases)
下载 `windows-x64-portable.zip`，完整解压后运行 `NEU-JWXT-Toolkit.exe`。
发行包已经包含后端、依赖和前端资源，不需要安装 Python 或 Node.js。程序只监听本机回环
地址，用户数据保存在 `%LOCALAPPDATA%\NEU-JWXT-Toolkit\data`。

项目目前没有 Authenticode 代码签名。请只从项目 Release 下载，并使用同一 Release 的
`SHA256SUMS.txt` 校验文件；项目不会要求关闭 Defender 或添加安全排除项。详见
[Windows 本地安装](docs/部署/Windows本地安装.md)。

### Linux 轻量服务

Linux 发行版面向同一位用户、一个 NEU 账号的多设备访问：

```bash
tar -xzf NEU-JWXT-Toolkit-<版本>-linux-amd64.tar.gz
cd neu-jwxt-toolkit
sudo ./install.sh
```

服务默认只监听 `127.0.0.1`。请使用 Caddy 或 Nginx 提供 HTTPS 反向代理，并设置安装脚本
要求的网站访问密码。程序数据位于 `/var/lib/neu-jwxt-toolkit`。详见
[Linux 轻量服务](docs/部署/Linux轻量服务.md)。

### Android

同一 Release 会提供两个可以直接安装的 APK：

| APK | 用途 |
|---|---|
| `android-client-arm64.apk` | 内置前端，连接用户配置的 HTTPS 服务端 |
| `android-local-arm64.apk` | 内置前端、Python 3.13 和完整 FastAPI 后端，数据留在手机 |

两者要求 Android 7.0（API 24）及以上和 `arm64-v8a` 设备，可以同时安装。APK 使用固定的
自签名密钥，首次安装只需允许系统安装未知来源应用；后续同包名版本可以直接覆盖安装。
本地版开启“打开时默认课表”且存在缓存时，会先显示本机缓存课表，不等待内置后端启动。
安装、通知权限、数据目录、更新和安全边界见 [Android 安装](docs/部署/Android安装.md)。

### 从源码运行

需要 Python 3.11、Node.js 20 和 npm：

```bash
git clone https://github.com/RekaYOO/NEU-JWXT-Toolkit.git
cd NEU-JWXT-Toolkit
cd frontend
npm ci
cd ..
python start_all.py
```

`start_all.py` 首次运行会创建 `.venv`、安装 `requirements.txt` 中的 Python 依赖并构建前端；
前端依赖需先用 `npm ci` 安装。常用参数：

```bash
python start_all.py           # 单端口，http://localhost:8000
python start_all.py --build   # 强制重建前端后启动
python start_all.py --dev     # 前后端开发模式，前端默认端口 3000
python start_all.py --port 8080
```

详细环境、手动启动和验证命令见[快速开始](docs/快速开始.md)。

### 更新方案

- **Windows 便携版**：先退出程序，再下载新版本完整 ZIP 并解压到新的目录后启动。不要只替换
  单个 EXE；用户数据独立保存在 `%LOCALAPPDATA%\\NEU-JWXT-Toolkit\\data`，更新不会覆盖登录、
  缓存、方案组或通知配置。下载后用同一 Release 的 `SHA256SUMS.txt` 校验。
- **Linux 服务**：下载新 tarball，解压后在新目录执行 `sudo ./install.sh --upgrade`。安装脚本
  会保留 `/var/lib/neu-jwxt-toolkit` 的数据和配置，并重启 systemd 服务；升级后先查看服务状态
  和健康检查，异常时可回退到上一份发行目录。详见[Linux 轻量服务](docs/部署/Linux轻量服务.md)。
- **Android**：下载与已安装版本相同包名的新 APK 并覆盖安装。客户端版和本地版互不替代；
  固定签名和递增 `ANDROID_VERSION_CODE` 用于保留应用内部数据。
- **源码运行**：在仓库目录执行 `git pull`，然后按锁文件重新安装前端依赖并重建：
  `cd frontend && npm ci && cd .. && python start_all.py --build`。不要为“更新代码”删除 `data/`
  或 `.venv`；只有明确要重置本地环境时才清理它们。

发行维护者的版本号、标签、GitHub Actions 和 Release 触发规则见[发行与 CI/CD](docs/开发/发行与CI.md)。

### GitHub Release

正式发布使用根目录 `VERSION` 作为唯一版本来源，并推送对应的 `v` 标签：

```bash
git pull origin main
version="$(tr -d '\r\n' < VERSION)"
git tag "v${version}"
git push origin "v${version}"
```

推送标签后，GitHub Actions 会依次完成前端、Linux 服务端、Windows 便携版、Android 双 APK、
模拟器验收、SHA-256 清单和 artifact attestation；全部成功后才创建 GitHub Release。标签必须与
`VERSION` 完全一致。普通提交和 Pull Request 只运行测试与调试构建，不会创建正式 Release。

## 使用前须知

- 校内网络可使用直连；校外访问可选择 WebVPN 密码、短信或微信扫码流程。遇到设备二次认证时，
  程序会显示学校实时返回的图形验证码，由用户手动填写并点击获取短信验证码。
  WebVPN 接口在保留中文提示的同时提供稳定 `error_code`，认证方式和会话
  恢复边界见 [WebVPN 校外访问](docs/功能/WebVPN校外访问.md)。
- 选课系统使用独立的 JWXK 服务会话。JWXK 直连不可达、WebVPN 登录失效和“账号不在当前
  选课轮次”会分别提示；学校系统不可用时仍可查看本地历史工作台。自动抢课、空位换课和
  策略投权因登录失效暂停时，可通过独立远程认证恢复页面完成二维码或短信验证，恢复前
  不会降级候选或重放写操作。
- 选课、退课、投权、报名、取消报名、评教和证明生成都属于远端写操作。界面会在提交前
  保留确认步骤；响应不明确时不会自动重放。
- 本地缓存用于加快读取，不能替代官方最终结果。涉及成绩、选课和考试的重要信息应在官方
  系统中复核。
- 打开课表时，若存在上次在线账号的浏览器课表快照，会先进入只读恢复态立即显示；教务认证在
  后台继续进行。认证未恢复时不会清空课表，但切换到其他页面或执行任何远端操作仍需重新登录。
- 账号、Cookie、SMTP 授权码、日志和业务缓存均属于敏感数据。不要公开 `data/`，也不要
  将其提交到 Git。

## 开发与验证

开始修改前请阅读[开发接手与变更规范](docs/开发/开发接手与变更规范.md)。项目是 React
前端与 FastAPI 后端组成的模块化单体，三种运行 profile 共享同一套业务实现。

与 GitHub CI 对齐的核心命令：

```bash
python -m pytest backend/tests
python -m compileall -q backend launchers

cd frontend
npm ci
npm test
npm run build
```

发行版本由根目录 `VERSION` 决定，Android 覆盖安装顺序由递增的 `ANDROID_VERSION_CODE` 决定；
匹配的 `v<版本>` 标签触发 Release workflow。完整流程见
[发行与 CI/CD](docs/开发/发行与CI.md)。

## 许可与免责声明

本项目采用 [MIT License](LICENSE)。
本项目仅供学习和个人教务辅助使用，与东北大学及其教务系统开发、运营单位无隶属或授权
关系。使用者应遵守学校规定并自行承担操作结果；课程容量、资格、成绩、考试安排和选课结果
均以官方系统为准。
