# Go 新版备份与恢复

使用同一 `toolbox` 程序的维护命令，不需要启动 HTTP 服务或提供学校账号。
这份备份用于 Go 新版数据接续；旧 Python 文件导入属于另一条迁移流程。

## 备份

候选包 14 起，备份命令以只读方式打开源库，不执行迁移；清单版本取自归档快照，快照中的加密凭据在封装前与密钥核对。

升级前，请用**目前正在使用的版本**备份，再让新版本接触数据目录。候选包 13 的 `backup` 打开数据库时会执行该版本的初始化和迁移，不能用新版程序对旧库做“迁移前备份”。若通过 Windows 启动器切换过版本，请使用当前选中版本的程序，不要假定根目录的 `toolbox.exe` 就是当前版本。

设置 `NEU_JWXT_BACKUP_PASSWORD` 环境变量，长度至少 12 字节。不要把密码直接写进
命令参数、脚本、仓库或交付包；备份文件与密码分开保管。PowerShell 7 可隐藏输入：

```powershell
$env:NEU_JWXT_BACKUP_PASSWORD = Read-Host '备份密码' -MaskInput
.\toolbox.exe backup --data (Join-Path $env:LOCALAPPDATA 'NEU-JWXT-Toolkit\data-go') --file 'D:\backups\toolbox-2026-11-16.age'
Remove-Item Env:NEU_JWXT_BACKUP_PASSWORD
```

Windows 示例针对默认本地数据目录；使用自定义目录时必须替换 `--data`。Linux 交互式 shell：

```bash
read -rsp 'Backup password: ' NEU_JWXT_BACKUP_PASSWORD
export NEU_JWXT_BACKUP_PASSWORD
./toolbox backup --data /var/lib/neu-toolbox --file /srv/backups/toolbox-2026-11-16.age
unset NEU_JWXT_BACKUP_PASSWORD
```

目标文件父目录须存在，已有同名文件不会被覆盖。维护命令只使用显式 `--data`，
不被服务运行时的 `NEU_JWXT_DATA_DIR` 改写。
可在服务运行时备份：SQLite 生成一致快照，包含已提交事务；稍后发生的修改不会补入。
备份不停止账号、不改变任务状态、不请求学校接口。

文件使用标准 age 口令加密，内部 ZIP 只含数据库、vault 密钥和校验清单。
包含所有账号资料、凭据、配置、缓存、删除记录、操作记录和站点配置，属于敏感资料。
不包含程序、前端、旧 Python 目录或浏览器 IndexedDB 镜像。
备份不包含更新下载目录。当前源码恢复时会清除副本中的旧更新任务；如需更新，请重新检查版本并下载。原安装目录的任务和启动选择不变。
当前数据库快照上限 512 MiB，超限明确失败，不生成不完整备份。

## 恢复与切换

停止要替换的服务并保留原目录。重新设置同一口令后，恢复到**不存在的新目录**：

```powershell
.\toolbox.exe restore --file 'D:\backups\toolbox-2026-11-16.age' --data 'D:\toolbox-restored'
```

```bash
./toolbox restore --file /srv/backups/toolbox-2026-11-16.age --data /var/lib/neu-toolbox-restored
```

成功后修改服务数据目录配置，再按备份原有部署模式启动。恢复不绕过单/多用户模式绑定，
不要直接覆盖正在运行的数据库。用完后清除密码环境变量。

Linux 示例按随包服务的 `/var/lib/neu-toolbox` 编写；如已自定义 `--data` 或数据目录环境变量，应使用实际目录。恢复切换后，请确保服务账号对恢复目录有读写权限；若启用了 systemd 的 `ProtectSystem=strict`，还须同步调整服务的可写目录配置，否则服务可能无法写入新路径。

恢复校验加密认证、固定文件清单、SHA-256、SQLite 完整性、账号外键及数据库和密钥匹配。
错误密码、损坏、未知/重复条目、目录穿越、缺失密钥和不支持的版本均拒绝。
校验失败不创建目标目录；已有目录不会被覆盖。

恢复保留账号准入、配置、资料及未知操作记录，但撤销旧网站/学校浏览器会话及恢复链接，
提升账号会话代数，暂停自动选课和成绩追踪，取消未发邮件。须重新登录并手动开启所需任务。
SMTP 加密配置保留；启用前可检查收件人。未知学校操作仍须核验，不能因恢复而重发。
恢复副本会把备份时仍在等待回执的选课操作记为“结果未知”，保留原尝试次数和课程标识。请先核对学校选课结果，不要根据这条记录判断成功或失败。

当前源码在恢复副本中同时清除独立选课 WebVPN 会话，避免重启时将旧网关列为后台恢复候选；原备份及原数据目录不变。候选包 14 仍依赖会话代数校验拒绝旧网关。

若文件发布中断，目标保留 `.restore-in-progress` 标记，程序拒绝启动该目录。
保留它排查，并重新恢复到另一个新目录；不要删除标记强行启动。

## 回退

停止新服务，将数据目录配置切回原目录。原目录不被恢复命令修改。
两份目录的新数据不会自动合并；切换前分别备份，并明确选择后续使用的数据源。
