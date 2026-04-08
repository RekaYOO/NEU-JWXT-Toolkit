#!/usr/bin/env python3
"""
一键启动前后端 (Windows & Linux/macOS)

使用:
    python start_all.py                    # 启动前后端（默认端口）
    python start_all.py --backend-port 8001 --frontend-port 3001  # 自定义端口
    python start_all.py backend            # 仅启动后端
    python start_all.py frontend           # 仅启动前端
    python start_all.py --help             # 查看帮助

环境变量:
    BACKEND_PORT  - 后端端口 (默认: 8000)
    FRONTEND_PORT - 前端端口 (默认: 3000)
    NODE_OPTIONS  - Node.js 选项 (默认: --openssl-legacy-provider)
"""

import argparse
import os
import platform
import subprocess
import sys
import time

# ── 配置 ──────────────────────────────────────────────────────────────────────

DEFAULT_BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))
DEFAULT_FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "3000"))

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def is_windows():
    """判断是否为 Windows 系统"""
    return platform.system() == "Windows"


def get_venv_python():
    """获取虚拟环境中的 Python 路径"""
    if is_windows():
        return os.path.join(".venv", "Scripts", "python.exe")
    else:
        return os.path.join(".venv", "bin", "python")


def get_venv_activate_cmd():
    """获取虚拟环境激活命令"""
    if is_windows():
        return os.path.join(".venv", "Scripts", "activate.bat")
    else:
        return f"source {os.path.join('.venv', 'bin', 'activate')}"


def start_backend(port=DEFAULT_BACKEND_PORT):
    """启动后端"""
    print("=" * 60)
    print(f"启动后端服务 (端口: {port})...")
    print("=" * 60)

    venv_python = get_venv_python()
    backend_main = os.path.join("backend", "main.py")

    if not os.path.exists(venv_python):
        print(f"错误: 虚拟环境未找到: {venv_python}")
        print("请先创建虚拟环境并安装依赖:")
        print("")
        print("  python -m venv .venv")
        if is_windows():
            print("  .venv\\Scripts\\pip install -r requirements.txt")
        else:
            print("  .venv/bin/pip install -r requirements.txt")
        print("")
        return None

    if not os.path.exists(backend_main):
        print(f"错误: 后端主文件未找到: {backend_main}")
        return None

    # 设置环境变量
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["BACKEND_PORT"] = str(port)

    # 在新窗口启动后端
    if is_windows():
        proc = subprocess.Popen(
            [venv_python, backend_main],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            env=env,
        )
    else:
        # Linux/macOS: 使用 gnome-terminal、konsole 或 xterm
        cmd = f'cd "{os.getcwd()}" && "{venv_python}" "{backend_main}"'
        terminal_cmd = None

        # 尝试各种终端模拟器
        for term in ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]:
            result = subprocess.run(
                ["which", term], capture_output=True, text=True
            )
            if result.returncode == 0:
                if term == "gnome-terminal":
                    terminal_cmd = [term, "--", "bash", "-c", f"{cmd}; read -p 'Press Enter to close...'"]
                elif term == "konsole":
                    terminal_cmd = [term, "-e", "bash", "-c", f"{cmd}; read -p 'Press Enter to close...'"]
                elif term == "xfce4-terminal":
                    terminal_cmd = [term, "-e", "bash", "-c", f"{cmd}; read -p 'Press Enter to close...'"]
                else:  # xterm
                    terminal_cmd = [term, "-e", "bash", "-c", f"{cmd}; read -p 'Press Enter to close...'"]
                break

        if terminal_cmd:
            proc = subprocess.Popen(terminal_cmd, env=env)
        else:
            # 如果没有图形终端，直接在后台运行
            proc = subprocess.Popen(
                [venv_python, backend_main],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

    print(f"后端已启动 (PID: {proc.pid})")
    print(f"API 地址: http://localhost:{port}")
    print(f"API 文档: http://localhost:{port}/docs")
    return proc


def start_frontend(port=DEFAULT_FRONTEND_PORT, backend_port=DEFAULT_BACKEND_PORT):
    """启动前端"""
    print("=" * 60)
    print(f"启动前端开发服务器 (端口: {port})...")
    print("=" * 60)

    frontend_dir = "frontend"
    if not os.path.exists(os.path.join(frontend_dir, "node_modules")):
        print(f"错误: 前端依赖未安装")
        print("请先运行: cd frontend && npm install")
        return None

    # 设置环境变量
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["FRONTEND_PORT"] = str(port)
    env["REACT_APP_BACKEND_PORT"] = str(backend_port)
    
    # 禁用 webpack dev server 的 host 检查，避免 allowedHosts 错误
    # 这是 React Scripts v5 的已知问题，仅在开发环境使用
    env["DANGEROUSLY_DISABLE_HOST_CHECK"] = "true"
    env["WDS_SOCKET_HOST"] = "localhost"

    # 默认 Node 选项，解决 OpenSSL 3.0 兼容性问题
    if "NODE_OPTIONS" not in env:
        env["NODE_OPTIONS"] = "--openssl-legacy-provider"

    # 在新窗口启动前端
    if is_windows():
        proc = subprocess.Popen(
            "npm start",
            cwd=frontend_dir,
            shell=True,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            env=env,
        )
    else:
        # Linux/macOS
        cmd = f'cd "{os.path.join(os.getcwd(), frontend_dir)}" && npm start'
        terminal_cmd = None

        for term in ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]:
            result = subprocess.run(
                ["which", term], capture_output=True, text=True
            )
            if result.returncode == 0:
                if term == "gnome-terminal":
                    terminal_cmd = [term, "--", "bash", "-c", f"{cmd}; read -p 'Press Enter to close...'"]
                elif term == "konsole":
                    terminal_cmd = [term, "-e", "bash", "-c", f"{cmd}; read -p 'Press Enter to close...'"]
                elif term == "xfce4-terminal":
                    terminal_cmd = [term, "-e", "bash", "-c", f"{cmd}; read -p 'Press Enter to close...'"]
                else:
                    terminal_cmd = [term, "-e", "bash", "-c", f"{cmd}; read -p 'Press Enter to close...'"]
                break

        if terminal_cmd:
            proc = subprocess.Popen(terminal_cmd, env=env)
        else:
            proc = subprocess.Popen(
                "npm start",
                cwd=frontend_dir,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

    print(f"前端已启动 (PID: {proc.pid})")
    print(f"访问地址: http://localhost:{port}")
    return proc


def main():
    parser = argparse.ArgumentParser(
        description="一键启动 NEU 教务系统工具箱前后端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python start_all.py                                    # 启动前后端
  python start_all.py --backend-port 8001                # 后端使用 8001 端口
  python start_all.py --frontend-port 3001               # 前端使用 3001 端口
  python start_all.py -b 8001 -f 3001                    # 同时指定前后端端口
  python start_all.py backend                            # 仅启动后端
  python start_all.py frontend                           # 仅启动前端

环境变量:
  BACKEND_PORT   后端端口 (默认: 8000)
  FRONTEND_PORT  前端端口 (默认: 3000)
        """,
    )

    parser.add_argument(
        "command",
        nargs="?",
        choices=["backend", "frontend"],
        help="只启动指定服务 (backend 或 frontend)",
    )
    parser.add_argument(
        "-b", "--backend-port",
        type=int,
        default=DEFAULT_BACKEND_PORT,
        help=f"后端端口 (默认: {DEFAULT_BACKEND_PORT})",
    )
    parser.add_argument(
        "-f", "--frontend-port",
        type=int,
        default=DEFAULT_FRONTEND_PORT,
        help=f"前端端口 (默认: {DEFAULT_FRONTEND_PORT})",
    )

    args = parser.parse_args()

    if args.command == "backend":
        start_backend(args.backend_port)
    elif args.command == "frontend":
        start_frontend(args.frontend_port, args.backend_port)
    else:
        # 启动前后端
        backend_proc = start_backend(args.backend_port)
        if backend_proc:
            time.sleep(2)  # 等待后端启动

        frontend_proc = start_frontend(args.frontend_port, args.backend_port)

        print()
        print("=" * 60)
        print("所有服务已启动!")
        print("=" * 60)
        print()
        print("访问地址:")
        print(f"  - 前端: http://localhost:{args.frontend_port}")
        print(f"  - 后端: http://localhost:{args.backend_port}")
        print()
        print("按 Ctrl+C 退出此窗口，服务将在后台运行")
        print()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在关闭...")


if __name__ == "__main__":
    main()
