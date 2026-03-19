#!/usr/bin/env python3
"""
一键启动前后端(windows)

使用:
    python start_all.py        # 启动前后端
    python start_all.py backend # 仅启动后端
    python start_all.py frontend # 仅启动前端
"""

import subprocess
import sys
import os
import time

def start_backend():
    """启动后端"""
    print("=" * 60)
    print("启动后端服务...")
    print("=" * 60)
    
    venv_python = os.path.join('.venv', 'Scripts', 'python.exe')
    backend_main = os.path.join('backend', 'main.py')
    
    if not os.path.exists(venv_python):
        print("错误: 虚拟环境未找到，请先运行: uv venv && uv pip install -r requirements.txt")
        return None
    
    # 在新窗口启动后端
    proc = subprocess.Popen(
        [venv_python, backend_main],
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )
    print(f"后端已启动 (PID: {proc.pid})")
    print("API 地址: http://localhost:8000")
    print("API 文档: http://localhost:8000/docs")
    return proc

def start_frontend():
    """启动前端"""
    print("=" * 60)
    print("启动前端开发服务器...")
    print("=" * 60)
    
    frontend_dir = 'frontend'
    if not os.path.exists(os.path.join(frontend_dir, 'node_modules')):
        print("错误: 前端依赖未安装，请先运行: cd frontend && npm install")
        return None
    
    # 在新窗口启动前端
    proc = subprocess.Popen(
        'npm start',
        cwd=frontend_dir,
        shell=True,
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )
    print(f"前端已启动 (PID: {proc.pid})")
    print("访问地址: http://localhost:3000")
    return proc

def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == 'backend':
            start_backend()
        elif cmd == 'frontend':
            start_frontend()
        else:
            print(f"未知命令: {cmd}")
            print("用法: python start_all.py [backend|frontend]")
    else:
        # 启动前后端
        backend_proc = start_backend()
        if backend_proc:
            time.sleep(2)  # 等待后端启动
        
        frontend_proc = start_frontend()
        
        print()
        print("=" * 60)
        print("所有服务已启动!")
        print("=" * 60)
        print()
        print("访问地址:")
        print("  - 前端: http://localhost:3000")
        print("  - 后端: http://localhost:8000")
        print()
        print("按 Ctrl+C 退出此窗口，服务将在后台运行")
        print()
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在关闭服务...")

if __name__ == "__main__":
    main()
