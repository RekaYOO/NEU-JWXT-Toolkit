# 发行与 CI/CD

## 版本来源

根目录 `VERSION` 是发行版本的唯一来源。后端健康检查、PyInstaller 程序和发行文件名均从该文件读取。Git 标签必须使用相同版本并带 `v` 前缀，例如 `v1.3.5`。

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
ISCC.exe "/DMyAppVersion=$version" "/DSourceDir=$pwd\dist\NEU-JWXT-Toolkit" packaging\windows\installer.iss
```

Linux：

```bash
pyinstaller packaging/pyinstaller/server.spec --clean --noconfirm
```

PyInstaller 使用 `onedir`，前端构建被复制进冻结目录。Windows 的同一 `onedir` 既用于便携 ZIP，也由 Inno Setup 生成按用户安装器；Linux 的 `onedir` 与安装脚本、systemd 单元和反代示例一起进入 `tar.gz`。

## 自动化

`.github/workflows/ci.yml` 在 push 和 pull request 上运行：

- 后端测试；
- React 生产构建；
- Python 编译检查；
- 静态首页挂载测试。

`.github/workflows/release.yml` 在推送 `v*` 标签时：

1. 只构建一次 React 静态资源并在任务间传递；
2. 分别构建 Windows x64 与 Linux amd64 冻结程序；
3. 对冻结程序执行健康检查、静态首页和关闭测试；
4. 生成安装器、便携 ZIP 与 Linux tar 包；
5. 生成 `SHA256SUMS.txt` 和 GitHub 构建来源证明；
6. 上传到同一个 GitHub Release。

手动触发工作流只构建并保留 Actions 产物，不自动创建 Release。

## 发行前检查

- Git 标签版本必须与 `VERSION` 完全一致。
- 发行包不得包含仓库 `data/`、`.env`、日志、真实凭据、会话或开发依赖。
- Windows 应在未安装 Python/Node.js 的环境验证安装版与便携版。
- Linux 应验证安装、重启、升级成功、健康检查失败回滚和两种反向代理。
- 正式模式只绑定 `127.0.0.1`，不启用跨域白名单以外的访问，也不公开 Swagger/OpenAPI。
