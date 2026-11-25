# Go + Web 运行包

## 从原版迁移

从 Python 原版切到 Go 版，请保留原程序、数据和配置，在独立目录试运行新版，再按[旧版资料导入](旧版资料导入.md)逐账号预览并导入。旧版登录状态、凭据和运行中的任务需要按导入说明重新处理。更新已有 Go 安装前，请按[备份与恢复](Go备份与恢复.md)制作可恢复的备份。

| 原有环境 | 切换方式 |
| --- | --- |
| Windows 原版 | 退出旧版后将 Go ZIP 解压到新目录，再运行新版启动器。默认数据目录为 `%LOCALAPPDATA%\NEU-JWXT-Toolkit\data-go`，旧资料通过 `import-legacy` 导入。两版共用默认本机端口，回退时先退出新版，再启动旧版。已有自定义 `NEU_JWXT_DATA_DIR` 时，先确认新版使用独立目录。 |
| Linux 原版 | 保留旧 `neu-jwxt-toolkit` 服务、更新监听、目录和反代配置，按下文的并行试运行步骤切换。旧服务的安装脚本和自动更新只用于旧 Python 包。 |
| Android 原版 | 覆盖安装需要相同包名和签名，版本码不得降低。正式包、调试包及本地资料迁移需要分别核验，换包前先保留原版。 |

导入前核对支持的资料和未迁移项。远程服务首次启动需要初始化网站访问密码和管理员密码。学校会话需通过新版登录重新建立，共享邮件、追踪和恢复设置需要显式指定归属。回退切换后，两边新增的本地资料不会自动合并。见[旧版资料导入](旧版资料导入.md)。

### Windows 更新与回退

1. 更新前先制作可恢复的备份。在系统设置里确认安装，安装器会用当前数据库的副本检查新版能否迁移；检查失败不会改变启动选择。使用命令行时，在原安装目录执行 `toolbox.exe install-update -archive "<Windows ZIP>" -sha256 <可信清单摘要> -check-data "<实际数据目录>"`；省略 `-check-data` 会跳过这项副本检查。
2. 从托盘或页面退出程序，再双击原安装目录的 `启动工具箱.exe`。旧包使用同目录的 `启动工具箱.cmd`。
3. Windows 本地单用户模式会在新版首次打开已有数据库前登记快照。若新版在确认健康前报错退出，且快照成功还原，启动器会重新选择上一安装（没有可用的上一安装则使用根程序）。看到“已恢复升级前数据，正在重新启动原版本”后，等待原版打开，再核对账号和最近一次保存的数据。

**没有自动恢复时：** 停止重试，保留启动窗口中的错误、原安装目录和完整数据目录。不要删除 `vault.key`、SQLite 日志文件或升级快照。快照与密钥不匹配、有未收拢的数据库日志等情况会阻止自动还原；强杀进程、断电和新版已确认健康后的故障也不在上述保障范围。需要恢复资料时按[备份与恢复](Go备份与恢复.md)将备份还原到新目录，保留原目录供核对。

**仅切回根程序：** 退出工具箱后在原安装目录执行 `toolbox.exe rollback-update`。它只改变启动选择，不恢复数据库，也不删除并列安装。若新版已迁移数据库，先核对旧版能否打开当前数据，或使用升级前的兼容备份。

运行包包含同一根目录 `VERSION` 对应的 Go 程序和生产网页。无需 Python、Node 或 Go 开发环境。
当前构建针对 Windows amd64 与 Linux amd64。Linux 一键安装使用系统自带或预装的 Python 3.11+。
可用 `toolbox.exe stage-update -archive <Windows发行ZIP> -sha256 <官方清单中的摘要> -parent <现有目录>` 校验并准备独立程序目录。命令输出新目录的位置，供手工试运行。摘要取自所下载版本的发行清单。

## 选择部署模式

| 使用场景 | 模式 | 首次使用 |
| --- | --- | --- |
| 个人电脑 | `local-single` | 双击 `启动工具箱.exe`，在本机浏览器登录。 |
| 一人通过网络访问 | `remote-single` | 在新数据目录初始化网站密码和管理员密码，通过 HTTPS 访问 `/admin`，添加自己的学号。 |
| 多人通过网络访问 | `remote-multi` | 使用随包 Linux 服务的默认模式，通过 HTTPS 访问 `/admin`，添加允许登录的学号。每位用户从站点首页用自己的学校账号登录。 |

远程部署的监听地址、数据目录、密码环境变量和反向代理配置见下方 Linux 服务与 HTTPS 章节。

## Windows 本机

解压后双击原安装目录中的 `启动工具箱.exe`。工具箱在后台运行，显示托盘并打开浏览器。需要查看启动诊断时运行 `启动工具箱.cmd`。在系统设置中确认安装更新后，请从托盘退出程序，再运行原目录的启动器。
托盘提供“打开工具箱”和“退出程序”。默认地址为 `http://127.0.0.1:8000`。
重复启动同一数据目录时会核验已运行的本地服务并打开其页面，不启动第二套后台任务。
自动化测试或无需桌面交互时使用 `--tray=false --open-browser=false`。
默认本地单用户模式，仅监听本机；在控制台按 Ctrl+C，或在页面确认“退出程序”后退出。
退出接口使用本次进程的随机令牌并核对监听地址及同源请求；远程部署不提供关闭服务能力。
所有运行模式均对数据目录持有操作系统文件锁；进程异常结束也会释放锁，不需要手动删除锁文件。
密码重置和仅初始化命令需要先停下使用同一数据目录的服务。
数据默认位于 `%LOCALAPPDATA%\NEU-JWXT-Toolkit\data-go`，独立于旧版数据目录。
程序按自身所在目录读取页面和版本，快捷方式的“起始位置”无需设置为解压目录。
使用 `toolbox.exe --version` 查看版本，不会打开或创建数据目录。

## Linux 服务

### 从 GitHub 一键安装

在 Linux amd64 的 systemd 主机上准备 Bash、`curl`、Python 3.11+ 和 `sudo`。安装时按提示输入公开 HTTPS 地址。安装入口从项目 [GitHub Releases](https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases) 首次发布 `install.sh` 附件起生效。

```bash
curl -fL --retry 3 --proto '=https' -o install.sh https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest/download/install.sh && bash ./install.sh
```

脚本读取该仓库最新正式 Release 的 Go 清单，再从清单对应标签下载 Linux 安装包。核验大小与 SHA-256 后安装 systemd 服务，检查版本和匿名访问保护。默认监听 `127.0.0.1:8000`。检测到旧 Python 服务时默认使用 `127.0.0.1:18000` 试运行，保留旧服务和原有反代，验证后按迁移步骤切换。可以用 `bash ./install.sh --help` 查看参数。无人值守安装传入 `--origin`，可配合 `--port`、`--mode remote-single` 和 `--version v<版本号>`。离线安装传入 `--archive <归档> --manifest <同版本官方清单>`。

初始网站密码和管理员密码保存在仅 root 可读的 `/etc/neu-toolbox.env`。按下文配置 HTTPS 反向代理，并验证站点及管理员登录。安装程序遇到已有的 Go 安装或数据目录会退出。若首次启动失败，查看 `journalctl -u neu-toolbox`，修正配置后可使用 `systemctl start neu-toolbox` 复验，再按需启用开机启动。

### 已有 Go 服务一键升级与回退

使用与一键安装相同的脚本。升级默认获取正式 Release，也可选定标签或使用已下载的官方归档和清单。

```bash
curl -fL --retry 3 --proto '=https' -o go-install.sh https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest/download/install.sh && bash ./go-install.sh update

bash ./go-install.sh update --version v2.3.0
bash ./go-install.sh update --archive ./Linux安装包.tar.gz --manifest ./go-release-manifest.json
bash ./go-install.sh status
bash ./go-install.sh recover
bash ./go-install.sh rollback --restore-data
```

`update` 只升级到更高版本，当前版本相同则直接退出。服务继续运行时，脚本先核验归档的大小、SHA-256 和候选程序，再用数据库副本检查兼容性。随后暂停服务，保存原程序、SQLite 数据库及密钥，然后启动新版。通过版本、首页和站点访问保护检查后，才开放业务并启动后台任务。检查失败时脚本恢复升级前的数据和原程序并重新启动。若维护意外中断，`status` 显示维护状态，`recover` 根据进度恢复旧版或完成已确认的切换。

`rollback --restore-data` 将程序和数据库切换到最近的检查点，另存当前版本和数据。回退会撤销会话和恢复链接、暂停选课及成绩追踪，取消待发送邮件。请重新登录，核对升级以来的变化，再按需继续任务。检查点存放在仅 root 可读的 `/opt/.neu-toolbox-upgrades/`，包含未加密的数据库与密钥，应与[加密备份](Go备份与恢复.md)配合使用。更早的检查点保留在原目录。新版已开放业务后，通过显式回退选择是否恢复旧数据。

自动维护支持标准的 `/opt/neu-toolbox`、`/var/lib/neu-toolbox`、`/etc/neu-toolbox.env` 和 `neu-toolbox.service` 布局，沿用原端口、部署模式、环境文件、开机启动状态和反代。扩展过服务单元、自定义数据目录或使用覆盖配置的实例，请参照手工流程。升级完成后可通过实际 HTTPS 地址核对登录。

### 手工安装与自定义服务升级

发行包附带 `neu-toolbox.service`。现有 Go 服务升级前，另存程序目录、systemd 单元、环境配置和一份可恢复的数据备份，再替换并验证新版。旧程序若无法读取迁移后的数据库，使用升级前的备份恢复到独立目录，详见[备份与恢复](Go备份与恢复.md#恢复与切换)。

新版在下载更新包时按任务编号暂存文件。下载或校验期间退出，下次启动会把任务标为失败，只清理该任务的 `payload.partial` 普通文件；不会扫描清理旧版随机目录，也不会删除已经生成的正式压缩包。中断不会自动续传或触发安装，请重新检查版本并下载。

Linux 原生包可在 Linux amd64 主机运行 `python3 tools/check_go_release.py dist/go-release --smoke`（目录参数为包含两个平台归档和校验清单的发行输出目录）。检查会将本平台归档解压到临时目录，用合成数据验证版本、健康检查、首页和正常退出；不依赖打包前的工作目录。仓库的 `Go Linux and Docker acceptance` 工作流分别验证 Linux 归档及 Docker 容器，容器检查涵盖非 root 用户、匿名访问限制、首页和重启后可用性。工作流存在不等于通过，验收时须核对对应版本的成功运行记录。

将解压目录内容放到 `/opt/neu-toolbox`，确保程序和前端资源归 root 所有且服务账号不可写。为服务创建独立系统用户和同名组 `neu-toolbox`。服务文件通过 systemd 的 `StateDirectory` 在启动前准备 `/var/lib/neu-toolbox`，所有者为服务账号，权限为 `0700`。
将附带 `neu-toolbox.service` 安装到 `/etc/systemd/system/`。
在仅 root 可读的 `/etc/neu-toolbox.env` 中设置：

```text
NEU_JWXT_PUBLIC_ORIGIN=https://你的工具箱域名
NEU_JWXT_ACCESS_PASSWORD=首次初始化的网站访问密码
NEU_JWXT_ADMIN_PASSWORD=首次初始化的管理员密码
```

两种密码支持 8–256 字符，建议至少 12 字符。首次启动会持久化密码哈希，后续修改环境变量不会静默重置已有密码。
修改需使用 `--reset-site-password access` 或 `--reset-site-password admin`，传入对应新密码环境变量。
运行 `systemctl daemon-reload` 和 `systemctl enable --now neu-toolbox`，使用
`journalctl -u neu-toolbox` 查看服务状态。远程单用户将服务参数改为 `--mode remote-single`；
一个已初始化的数据目录不能通过参数切换部署模式。

在主机本地用 `systemctl is-active neu-toolbox` 确认服务已启动，然后用 `curl -fsS http://127.0.0.1:8000/api/health` 核对版本及 `backend: go`。未登录时访问 `/api/status` 应返回 HTTP 401。健康检查通过不等于网站密码、反向代理或学校登录已通过验收，还需通过公开 HTTPS 地址完成登录流程。

首次确认网站访问密码和管理员密码均可登录后，可从 `/etc/neu-toolbox.env` 移除 `NEU_JWXT_ACCESS_PASSWORD` 和 `NEU_JWXT_ADMIN_PASSWORD` 两行。保留环境文件、`NEU_JWXT_PUBLIC_ORIGIN` 和原数据目录，不要删除 `vault.key`。运行 `systemctl restart neu-toolbox`，重复健康检查，并用未登录请求确认 `/api/status` 仍返回 401。最后在新的浏览器隐私窗口通过 HTTPS 用原密码登录，分别验证站点访问和管理员权限。若重启后提示需要初始化密码，先核对服务是否仍指向原数据目录，不要用一个新目录替代原库。

### Linux 原版并行试运行与切换

1. 确认旧服务的实际数据路径、监听端口、反代站点和单元启用状态。默认旧程序在 `/opt/neu-jwxt-toolkit`，数据在 `/var/lib/neu-jwxt-toolkit`，配置在 `/etc/neu-jwxt-toolkit/`。备份数据、配置、反代规则及旧单元，包括 `neu-jwxt-toolkit-updater.path` 和对应的更新服务。
2. 使用一键命令创建 Go 安装。若手工安装，在首次启动前修改 `/etc/systemd/system/neu-toolbox.service` 中的 `ExecStart`，示例端口 `18000` 须空闲。修改后执行 `systemctl daemon-reload`。选定部署模式后沿用该模式。试运行使用 `systemctl start neu-toolbox`，并用实际端口替换健康检查和预演反代中的端口。

   ```ini
   ExecStart=/opt/neu-toolbox/toolbox --mode remote-multi --listen 127.0.0.1:18000 --data /var/lib/neu-toolbox
   ```

3. 用独立的 HTTPS 预演入口验证站点密码、管理员准入和账号登录。站点地址须与 `NEU_JWXT_PUBLIC_ORIGIN` 一致。使用独立的迁移脚本预览并导入，确认新版可读取导入结果。见[旧版资料导入](旧版资料导入.md#linux-独立迁移脚本)。
4. 正式切换前暂停旧版自动任务。停用 `neu-jwxt-toolkit-updater.path` 并确认更新服务已退出，再停止 `neu-jwxt-toolkit.service`，按需同步最后变更的旧资料。保留旧服务的开机设置记录并停用它，避免重启后自动恢复运行。将新版的公开地址改为正式域名后重启新版，再将正式站点的反代上游改至 `127.0.0.1:18000` 并重新加载反代。核对健康、匿名 401、HTTPS 登录和导入资料后，再启用 `neu-toolbox.service` 开机启动。
5. 回退时先停止并停用新版，恢复旧反代配置和旧服务原有的启用状态，启动旧服务并验证，再按原状态恢复更新监听。保留新版数据供核对，必要时另行接续切换后新增的资料。

## HTTPS 反向代理


让 Caddy/Nginx 代理到 `127.0.0.1:8000`。Caddy 示例：

```caddyfile
你的工具箱域名 {
    reverse_proxy 127.0.0.1:8000
}
```

公开地址必须与实际 HTTPS 站点一致。默认仅信任回环代理；其他拓扑需用
`--trusted-proxies` 明确指定代理地址网段，不应信任任意来源。
远程模式先登录网站访问密码，再登录学校账号；多用户模式由管理员准入账号。
包 10 之后的开发版首次打开未记录部署模式的数据目录时，若其中已有多个账号，两种单用户模式均拒绝初始化。请保留原目录并使用远程多用户模式；不要通过删除账号来切换部署方式。
包 9 之后的开发版在启用站点访问密码时，管理页提供“退出站点”，同时撤销当前浏览器的站点访问和管理会话；再次输入站点密码不恢复管理权限。仅退出管理不结束普通站点访问。这两个入口不代替学校账号退出。

## Docker

源码仓库中设置上述三个环境变量，执行：

```sh
docker compose -f packaging/go/compose.yaml up --build -d
```

配置构建同版本 Go/Web 镜像，容器以 UID/GID 10001 运行，持久卷挂载 `/data`，
宿主机仅发布回环端口，HTTPS 仍由宿主机反向代理提供。绑定挂载时须预先赋予该 UID 写权限。
不要将真实凭据写进 compose、Dockerfile 或镜像；环境配置文件不得提交版本控制。
新数据卷首次启动必须提供两种初始密码。初始化后可从部署环境移除它们，保留同一命名卷并运行 `docker compose -f packaging/go/compose.yaml up -d --force-recreate`，让新容器不再携带初始密码。不要使用 `down -v`，它会删除数据卷。

## 升级、迁移与备份

系统设置可主动检查官方 Go/Web 发行，提供对应平台更新包时可点击下载并查看进度。
远程部署仅管理员能下载或读取下载进度。校验成功后显示服务器上的文件位置，
不会自动解压或安装；正常下载失败的部分文件会移除。任务记录和状态历史会保留到重启后，
中断的下载不会自动续传，会显示失败并等待手动重试；已完成文件会重新校验，丢失或损坏时
要求重新下载。加密数据备份不包含下载包，恢复数据后可重新下载。

标准 Linux 服务按[一键维护流程](#已有-go-服务一键升级与回退)完成切换。手工升级时先停止服务并执行加密备份，再替换程序目录，保留原数据目录及其 `vault.key`。
不得只复制数据库而丢失密钥；恢复到新目录后再切换数据路径。
完整命令见附带 `BACKUP.md`、`IMPORT.md`。源代码文档对应
`docs/部署/Go备份与恢复.md` 和 `docs/部署/旧版资料导入.md`。
默认数据路径可由 `--data` 指定，`NEU_JWXT_DATA_DIR` 环境变量优先级最高。
旧版数据使用预览摘要导入，不直接把旧目录当新版数据库目录。

## 从源码构建

安装 Go 1.24 或更高版本、Node 22 或更高版本以及 Python 3.11 或更高版本，先在
`frontend` 执行 `npm ci`，再运行 `python tools/build_go_release.py`。
可用 `--go` 指定 Go 路径、`--output` 指定新的输出目录；拒绝覆盖已有输出目录。
构建脚本重建网页，运行 Go 测试与静态检查，生成两个平台的包、依赖许可证清单、
`go-release-manifest.json` 和 `SHA256SUMS`。该清单用于重构包核验，不冒充旧版自动更新清单。
