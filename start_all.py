#!/usr/bin/env python3
"""
一键启动脚本

默认模式（单端口，推荐日常使用）:
    python start_all.py              # 自动 build 前端（如需要）→ 启动后端
    python start_all.py --build      # 强制重新构建前端后启动
    python start_all.py --port 8080  # 自定义端口

开发模式（双端口热重载）:
    python start_all.py --dev                    # 启动后端 + 前端 dev server
    python start_all.py --dev -b 8001 -f 3001    # 自定义端口
"""

import argparse
import atexit
import os
import platform
import subprocess
import sys
import time

# ── 配置 ──────────────────────────────────────────────────────────────────────

DEFAULT_PORT = 8000
DEFAULT_BACKEND_PORT = 8000
DEFAULT_FRONTEND_PORT = 3000

_procs = []


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def is_windows():
    return platform.system() == "Windows"


def get_venv_python():
    if is_windows():
        return os.path.join(".venv", "Scripts", "python.exe")
    return os.path.join(".venv", "bin", "python")


def _get_file_mtime(path):
    """获取文件修改时间，文件不存在返回 0"""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def _get_git_src_fingerprint():
    """获取 frontend/src/ 的 git 状态指纹（O(1)）

    已提交状态 → tree hash
    有未提交修改 → tree-hash + diff-hash
    无 git → 空字符串
    """
    try:
        # 获取已提交状态的 tree hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD:frontend/src"],
            capture_output=True,
            cwd=".",
            timeout=3,
        )
        if result.returncode != 0:
            return ""
        tree_hash = result.stdout.decode("utf-8", errors="replace").strip()

        # 检查是否有未提交的修改
        diff_result = subprocess.run(
            ["git", "diff", "--", "frontend/src/"],
            capture_output=True,
            cwd=".",
            timeout=3,
        )
        diff_text = diff_result.stdout.decode("utf-8", errors="replace")
        if diff_text:
            import hashlib
            diff_hash = hashlib.md5(diff_text.encode("utf-8")).hexdigest()
            return f"{tree_hash}-{diff_hash}"
        return tree_hash
    except Exception:
        return ""


def needs_build():
    """检查前端是否需要重新构建（O(1) 速度）

    检测逻辑：
    1. build 产物不存在 → 需要 build
    2. .buildinfo 不存在 → 需要 build
    3. package.json / package-lock.json 有更新 → 需要 build
    4. 有 git：frontend/src/ 的 git 指纹变了 → 需要 build
    5. 无 git：入口文件 index.js 比 build 新 → 需要 build
    """
    build_index = os.path.join("frontend", "build", "index.html")
    if not os.path.exists(build_index):
        return True

    buildinfo_path = os.path.join("frontend", "build", ".buildinfo")
    if not os.path.exists(buildinfo_path):
        return True

    try:
        import json
        with open(buildinfo_path, "r", encoding="utf-8") as f:
            info = json.load(f)
    except Exception:
        return True

    # 1. 依赖配置是否更新
    pkg_mtime = max(
        _get_file_mtime(os.path.join("frontend", "package.json")),
        _get_file_mtime(os.path.join("frontend", "package-lock.json")),
    )
    if pkg_mtime > info.get("pkg_mtime", 0):
        return True

    # 2. 有 git 时：检查 src/ 指纹（tree-hash 或 tree-hash+diff-hash）
    if os.path.exists(".git"):
        current_fp = _get_git_src_fingerprint()
        if current_fp and current_fp != info.get("src_fingerprint", ""):
            return True
        return False

    # 3. 无 git：只检查入口文件（O(1)，不完全准确但足够快）
    entry = os.path.join("frontend", "src", "index.js")
    if os.path.exists(entry):
        if os.path.getmtime(entry) > info.get("build_time_epoch", 0):
            return True

    return False


def build_frontend():
    """构建前端产物"""
    print("=" * 60)
    print("正在构建前端...")
    print("=" * 60)

    frontend_dir = "frontend"
    if not os.path.exists(os.path.join(frontend_dir, "node_modules")):
        print("错误: 前端依赖未安装，请先运行: cd frontend && npm install")
        return False

    env = os.environ.copy()
    # 使用相对路径，支持同源部署
    env["REACT_APP_API_URL"] = ""
    if "NODE_OPTIONS" not in env:
        env["NODE_OPTIONS"] = "--openssl-legacy-provider"

    try:
        # Windows 上 npm 是 npm.cmd，shell=True 更可靠
        subprocess.run(
            "npm run build",
            cwd=frontend_dir,
            env=env,
            shell=True,
            check=True,
        )

        # 写入 buildinfo，记录版本信息
        import json
        buildinfo = {
            "src_fingerprint": _get_git_src_fingerprint(),
            "pkg_mtime": max(
                _get_file_mtime(os.path.join(frontend_dir, "package.json")),
                _get_file_mtime(os.path.join(frontend_dir, "package-lock.json")),
            ),
            "build_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "build_time_epoch": time.time(),
        }
        buildinfo_path = os.path.join(frontend_dir, "build", ".buildinfo")
        with open(buildinfo_path, "w", encoding="utf-8") as f:
            json.dump(buildinfo, f, indent=2)

        print("=" * 60)
        print("前端构建完成")
        print("=" * 60)
        return True
    except subprocess.CalledProcessError as e:
        print(f"前端构建失败: {e}")
        return False


def start_process(cmd, cwd=None, env=None, shell=False):
    """启动子进程，输出直接打印到当前控制台"""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        shell=shell,
        env=merged_env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    _procs.append(proc)
    return proc


def cleanup():
    """退出时清理所有子进程"""
    for proc in _procs:
        if proc.poll() is None:
            try:
                if is_windows():
                    # /T 表示同时终止子进程
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                    )
                else:
                    proc.terminate()
                    proc.wait(timeout=3)
                    if proc.poll() is None:
                        proc.kill()
            except Exception:
                pass


atexit.register(cleanup)


# ── 启动函数 ──────────────────────────────────────────────────────────────────

def start_backend(port=DEFAULT_BACKEND_PORT):
    """启动后端服务"""
    venv_python = get_venv_python()
    backend_main = os.path.join("backend", "app", "main.py")

    if not os.path.exists(venv_python):
        print(f"错误: 虚拟环境未找到: {venv_python}")
        print("请先创建虚拟环境并安装依赖:")
        print("  python -m venv .venv")
        if is_windows():
            print("  .venv\\Scripts\\pip install -r requirements.txt")
        else:
            print("  .venv/bin/pip install -r requirements.txt")
        return None

    if not os.path.exists(backend_main):
        print(f"错误: 后端主文件未找到: {backend_main}")
        return None

    env = {"PORT": str(port), "BACKEND_PORT": str(port)}
    return start_process([venv_python, backend_main], env=env)


def start_frontend(port=DEFAULT_FRONTEND_PORT, backend_port=DEFAULT_BACKEND_PORT):
    """启动前端开发服务器"""
    frontend_dir = "frontend"
    if not os.path.exists(os.path.join(frontend_dir, "node_modules")):
        print("错误: 前端依赖未安装，请先运行: cd frontend && npm install")
        return None

    env = {
        "PORT": str(port),
        "FRONTEND_PORT": str(port),
        "REACT_APP_BACKEND_PORT": str(backend_port),
        "DANGEROUSLY_DISABLE_HOST_CHECK": "true",
        "WDS_SOCKET_HOST": "localhost",
    }
    if "NODE_OPTIONS" not in os.environ:
        env["NODE_OPTIONS"] = "--openssl-legacy-provider"

    return start_process("npm start", cwd=frontend_dir, shell=True, env=env)


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="一键启动 NEU 教务系统工具箱",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python start_all.py                          # 默认模式：单端口（自动 build）
  python start_all.py --build                  # 强制重新构建前端后启动
  python start_all.py --port 8080              # 使用 8080 端口
  python start_all.py --dev                    # 开发模式：双端口热重载
  python start_all.py --dev -b 8001 -f 3001    # 开发模式自定义端口

说明:
  默认模式 : 只启动后端(8000)，后端挂载前端 build 产物
             访问 http://localhost:8000
  开发模式 : 同时启动后端(8000)和前端 dev server(3000)
             访问 http://localhost:3000
        """,
    )

    parser.add_argument(
        "--dev",
        action="store_true",
        help="开发模式：启动前后端双服务（热重载）",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="强制重新构建前端",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"服务端口（默认模式，默认 {DEFAULT_PORT}）",
    )
    parser.add_argument(
        "-b", "--backend-port",
        type=int,
        default=DEFAULT_BACKEND_PORT,
        help=f"后端端口（开发模式，默认 {DEFAULT_BACKEND_PORT}）",
    )
    parser.add_argument(
        "-f", "--frontend-port",
        type=int,
        default=DEFAULT_FRONTEND_PORT,
        help=f"前端端口（开发模式，默认 {DEFAULT_FRONTEND_PORT}）",
    )

    args = parser.parse_args()

    if args.dev:
        # ── 开发模式 ──────────────────────────────────────────────────────────
        print("=" * 60)
        print("开发模式：启动后端 + 前端 dev server")
        print("=" * 60)

        backend_proc = start_backend(args.backend_port)
        if not backend_proc:
            sys.exit(1)

        time.sleep(2)  # 等待后端就绪

        frontend_proc = start_frontend(args.frontend_port, args.backend_port)
        if not frontend_proc:
            cleanup()
            sys.exit(1)

        print()
        print("=" * 60)
        print("所有服务已启动")
        print("=" * 60)
        print(f"后端 API : http://localhost:{args.backend_port}")
        print(f"前端页面 : http://localhost:{args.frontend_port}")
        print("按 Ctrl+C 退出")
        print()

        try:
            while True:
                # 监控进程状态，如果有进程退出则整体退出
                if backend_proc.poll() is not None:
                    print(f"\n后端进程已退出 (code: {backend_proc.poll()})")
                    break
                if frontend_proc.poll() is not None:
                    print(f"\n前端进程已退出 (code: {frontend_proc.poll()})")
                    break
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n正在关闭...")
    else:
        # ── 默认模式（单端口）─────────────────────────────────────────────────
        if args.build or needs_build():
            if not build_frontend():
                sys.exit(1)

        print("=" * 60)
        print("默认模式：启动单端口服务（后端挂载前端静态文件）")
        print("=" * 60)

        proc = start_backend(args.port)
        if not proc:
            sys.exit(1)

        print()
        print("=" * 60)
        print("服务已启动")
        print("=" * 60)
        print(f"访问地址 : http://localhost:{args.port}")
        print("按 Ctrl+C 退出")
        print()

        try:
            proc.wait()
        except KeyboardInterrupt:
            print("\n正在关闭...")


if __name__ == "__main__":
    main()
