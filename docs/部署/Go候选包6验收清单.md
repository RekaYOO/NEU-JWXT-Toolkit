# Go 候选包 6 验收记录

版本 2.2.4，目录 `dist/go-release-candidate-6/`。这是候选包，不是正式发布。

| 归档 | SHA-256 |
| --- | --- |
| `NEU-JWXT-Toolkit-2.2.4-windows-amd64-go.zip` | `a9d19ebdc988150161e5943d948bb5d9f0824339144874782738663bc0214ac8` |
| `NEU-JWXT-Toolkit-2.2.4-linux-amd64-go.tar.gz` | `06995c823b3834ddd36bbf064b7e219f6abf208adf18d3e1257e5cc25dc51ba3` |

## 验证范围

- 两个归档均通过文件校验。Windows 实际解压启动，验证首页、健康检查、退出、加密备份恢复、旧 GPA 导入、所有者与冲突处理，以及远程单用户和多用户准入与重启。结果见同目录 `verification.json`。远程准入使用回环 HTTP，不代表公网 TLS 实测。
- Chrome 在 320×800 和 1440×900 下使用包内前端和模拟接口下载培养计划 ZIP，文件名与字节一致；这不是 Android 原生下载验收。
- 源码级：Go 测试与 vet 通过；前端 65 组、603 项通过，结果见 `dist/frontend-regression-candidate-6.json`。仍有测试进程退出和构建大文件提示。

## 正式交付前的缺口

自动更新目前仍须手动替换，安装与失败回滚尚未实现。独立子服务密码恢复、异常登录重试、旧浏览器偏好和 IndexedDB 接续、各业务页面主题和键盘操作仍需逐项核验。旧插件生命周期与计划书的差异尚未解决。Linux、systemd、Docker 和 Android 连接服务端均缺少目标环境运行证据。
