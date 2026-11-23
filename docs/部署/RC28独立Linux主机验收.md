# RC28 独立 Linux 主机验收

推荐使用带 systemd 的一次性 x86_64 Linux 虚拟机，例如 Ubuntu 24.04。准备 Python 3.12 或更高版本。在普通用户目录解压 `RC28-linux-acceptance.zip`，进入解压目录后运行以下命令。

```sh
sha256sum --check SHA256SUMS && python3 linux-acceptance/tools/check_go_release.py . --smoke --report linux-verification.json
```

工具会在临时目录创建合成数据，通过回环地址启动 Linux 程序，核验启动与退出、备份恢复、迁移回退及准入重启。成功时保留 `linux-verification.json`，核对版本 `2.2.4` 和 `smoke.platform` 的 `linux` 值。系统服务安装及整机重启须另行验收，操作前先在目标主机确认服务目录和端口可用。

## 服务与重启

在同一台获准重启的测试主机上，按 Linux 归档内 `README.md` 的 systemd 部署步骤安装服务。以下命令在验收目录运行，记录首次启动时的系统启动标识，并检查实际运行程序、访问限制及数据目录权限。

```sh
set -eu
systemctl is-enabled --quiet neu-toolbox.service
systemctl is-active --quiet neu-toolbox.service
cat /proc/sys/kernel/random/boot_id > first-boot-id.txt
test "$(stat -c '%a %U %G' /var/lib/neu-toolbox)" = '700 neu-toolbox neu-toolbox'
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/status)" = 401
curl -fsS http://127.0.0.1:8000/api/health > first-health.json
python3 -c 'import json; h=json.load(open("first-health.json")); assert (h["backend"], h["version"]) == ("go", "2.2.4")'
```

完成整机重启后，回到同一验收目录执行以下检查。保留两份健康响应和两次启动标识，以便复核开机自启。最后按包内说明使用原站点密码及管理员密码分别登录，核对权限恢复情况。

```sh
set -eu
cat /proc/sys/kernel/random/boot_id > second-boot-id.txt
test "$(cat first-boot-id.txt)" != "$(cat second-boot-id.txt)"
curl -fsS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8000/api/health > second-health.json
python3 -c 'import json; h=json.load(open("second-health.json")); assert (h["backend"], h["version"]) == ("go", "2.2.4")'
systemctl is-active --quiet neu-toolbox.service
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/status)" = 401
```
