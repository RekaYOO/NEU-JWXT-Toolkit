# Go 候选包 8 验收清单

版本号仍为 `2.2.4`。以 `dist/go-release-candidate-8/` 及下列摘要识别本次构建，不用版本号区分旧候选包。本包未正式发布。

| 文件 | SHA-256 |
| --- | --- |
| `NEU-JWXT-Toolkit-2.2.4-windows-amd64-go.zip` | `59d98833cd4c6551706d13278f3b6bd47c897dadeb4d9e5bee4b6a2a27c1d52d` |
| `NEU-JWXT-Toolkit-2.2.4-linux-amd64-go.tar.gz` | `1d12920fc9d3f282189523abfe7aafdc26abc72c790a4174cbcd01527747d5f5` |

## 证据与边界

- `verification.json`：Windows ZIP 实际解压启动，通过首页、健康检查、正常退出、加密备份恢复、旧 GPA 导入及所有者与冲突处理、远程单用户和多用户准入与重启。测试使用合成数据和回环连接，不是公网 TLS 验收。
- Chrome 使用包内生产前端、模拟接口：课表与培养计划的确认和暂缓更新均覆盖 320、375、430、768、1440 像素宽度，实际点击后检查页面保留旧值或显示新值。培养计划下载另核对了 320 和 1440 像素宽度的文件名和字节。复验入口：`tools/check_merged_update_dialogs.cjs`，分别不加参数、加 `--defer-update` 和 `--report-download`。
- 收录了旧列设置读取、离线课表恢复标记与头像账号隔离修复。源码前端回归 66 组、609 项通过，证据为 `dist/frontend-regression-after-candidate-7.json`；不等于已在包内完成全部双账号操作。
- Linux 归档通过结构与摘要检查，Linux/systemd、Docker、Android 连接服务端仍缺目标环境运行证据。Windows 的网页安装确认、已有数据库升级失败后的恢复仍未验收。

## 包后修改

科研详情迟到响应处理及同页面收藏保存期间的操作限制属于包后源码，不在本次归档中。对应组件测试通过；服务端同账号并发收藏两门课题另有接口测试，不代表两个标签页会自动同步显示。
