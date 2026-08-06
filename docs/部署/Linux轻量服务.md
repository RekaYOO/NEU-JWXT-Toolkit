# Linux 轻量服务

Linux 发行版面向“同一位用户、一个 NEU 账号、多个设备”的轻量自托管场景。首版仅支持带 systemd 的 x86_64（amd64）Linux。程序以独立的无登录系统用户运行，不需要 Docker。

## 安装

```bash
tar -xzf NEU-JWXT-Toolkit-<版本>-linux-amd64.tar.gz
cd neu-jwxt-toolkit
sudo ./install.sh
```

安装脚本会询问监听端口（默认 `8000`）和网站访问密码。密码至少 8 个字符，输入时不会回显；配置只保存随机盐和 `scrypt` 哈希，不保存明文。

应用自行解析反向代理转发头，并在访问日志中同时保留原始访问 IP 和直连代理 IP。`trusted_proxies` 必须只包含直接连接应用的 Caddy/Nginx 地址；默认回环代理部署无需扩大信任范围。

`/var/lib/neu-jwxt-toolkit/logs/` 包含 IP、User-Agent、网站访问会话 ID 和已登录学号，属于敏感数据。日志 API 与日志页面受网站访问密码保护；不要把日志目录暴露为静态文件，也不要将日志上传到公开仓库。日志按天生成，达到大小上限后分片，并自动清理超过保留期的文件。

安装完成后的目录为：

```text
/opt/neu-jwxt-toolkit/          程序与反代示例
/etc/neu-jwxt-toolkit/          服务配置
/var/lib/neu-jwxt-toolkit/      会话、凭据、成绩和日志数据
```

配置和数据目录仅允许服务用户访问。服务默认只监听 `127.0.0.1`，可先在服务器本机检查：

```bash
curl http://127.0.0.1:8000/api/health
sudo systemctl status neu-jwxt-toolkit
```

`server` profile 会在启动前校验最终监听地址；配置文件或 `HOST` 环境变量中的非回环地址
都会被拒绝，避免误配置后绕过 HTTPS 反向代理直接暴露个人数据。允许 IPv4 回环地址、
`::1` 和 `localhost`。如需跨主机反代，应使用防火墙/隧道把代理安全地终止在本机，不能
把应用改为监听 `0.0.0.0`。

## 配置反向代理

首版只支持独立子域名，例如 `jwxt.example.com`，不支持部署到 `/jwxt/` 子路径。

### Caddy

编辑安装后的 `/opt/neu-jwxt-toolkit/examples/Caddyfile`，替换域名和端口，再合并到 Caddy 配置：

```caddyfile
jwxt.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

### Nginx

复制 `/opt/neu-jwxt-toolkit/examples/nginx.conf`，替换域名、证书路径和端口。示例已传递 `Host`、客户端地址和 HTTPS 协议信息。

反代必须使用 HTTPS。服务只信任配置中的本机代理地址；收到可信代理的 HTTPS 信息后，访问会话 Cookie 会自动带上 `Secure`。

## 两层登录

首次访问先显示网站访问密码页，验证后才会出现原有 NEU 登录页。访问会话 Cookie 为 HMAC 签名、`HttpOnly`、`SameSite=Lax`，有效期 7 天；连续输错 5 次后，同一来源会暂停尝试 5 分钟。

网站访问密码用于阻挡公开互联网对业务接口的直接访问，不能替代 HTTPS、防火墙、系统更新和妥善保管 NEU 凭据。

## 升级和自动回滚

下载并解压新版本，然后在新包目录执行：

```bash
sudo ./install.sh --upgrade
```

升级需要用户手动下载新版；脚本负责自动替换和失败回滚，不会在后台自动下载更新。

当 `/etc/neu-jwxt-toolkit/config.json` 已存在时，脚本不会调用初始化配置，也不会再次
询问端口或网站访问密码。以下内容全部延续：

- 配置文件中的端口、监听地址、可信代理、网站密码盐/哈希和 Cookie 签名密钥；
- `/var/lib/neu-jwxt-toolkit` 中的 NEU 会话、凭据、成绩、培养计划、追踪配置和日志；
- 系统中实际启用的 Caddy/Nginx 配置、域名和证书。

配置文件由安装脚本在服务启动前设置为 `neu-jwxt:neu-jwxt` 和 `0600`。运行中的服务只
读取该文件，不会在 systemd 的只读 `/etc` 沙箱内尝试修改权限。

升级只替换 `/opt/neu-jwxt-toolkit/app`，并更新包内示例和 systemd 服务文件。新程序
启动后会按照现有配置中的真实监听地址和端口检查 `/api/health`，最长等待约 60 秒。
复制新程序、更新服务文件或启动过程发生错误时会立即回滚；健康检查不通过时，脚本会
先输出 `systemctl status` 和最近的服务日志，再自动恢复上一版程序及 systemd 服务
文件，同时继续使用原配置和数据。只有配置文件不存在时才会进入首次配置流程。

## 卸载

在任意已解压的同版本发行包目录执行：

```bash
sudo ./uninstall.sh
```

默认删除程序和 systemd 服务，但保留配置、会话和业务数据。只有在二次提示中准确输入 `DELETE`，脚本才会删除 `/etc/neu-jwxt-toolkit`、`/var/lib/neu-jwxt-toolkit` 和服务用户；该操作不可恢复。

## 常用命令

```bash
sudo systemctl restart neu-jwxt-toolkit
sudo journalctl -u neu-jwxt-toolkit -n 100 --no-pager
sudo systemctl enable --now neu-jwxt-toolkit
```

