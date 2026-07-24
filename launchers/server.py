"""Linux server launcher and lightweight configuration utility."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_CONFIG = Path("/etc/neu-jwxt-toolkit/config.json")


def _write_config(config_path: Path, port: int) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("监听端口必须在 1 到 65535 之间")

    password = getpass.getpass("访问密码（至少 8 个字符）: ")
    confirmation = getpass.getpass("再次输入访问密码: ")
    if password != confirmation:
        raise ValueError("两次输入的访问密码不一致")

    from backend.core.runtime.access import hash_access_password

    payload = {
        "profile": "server",
        "host": "127.0.0.1",
        "port": port,
        "access_password": hash_access_password(password),
        "session_secret": secrets.token_urlsafe(48),
        "trusted_proxies": ["127.0.0.1", "::1"],
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(config_path)


def _serve(config_path: Path) -> int:
    os.environ["NEU_JWXT_PROFILE"] = "server"
    os.environ["NEU_JWXT_CONFIG"] = str(config_path)

    from backend.core.runtime import get_runtime_config

    config = get_runtime_config()
    if not (
        config.access_password_salt
        and config.access_password_hash
        and config.session_secret
    ):
        print(f"配置不完整，请先运行 init-config: {config_path}", file=sys.stderr)
        return 2

    import uvicorn
    from backend.app.main import app

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        proxy_headers=True,
        forwarded_allow_ips=",".join(config.trusted_proxies),
        log_level="info",
    )
    return 0


def _healthcheck(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 0 if response.status == 200 else 1
    except (OSError, urllib.error.URLError):
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="NEU 教务工具箱轻量服务")
    parser.add_argument("--version", action="store_true")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="启动服务")
    serve_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    init_parser = subparsers.add_parser("init-config", help="交互式生成服务配置")
    init_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    init_parser.add_argument("--port", type=int, default=8000)

    health_parser = subparsers.add_parser(
        "healthcheck",
        help="检查已安装服务是否健康",
    )
    health_parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000/api/health",
    )

    args = parser.parse_args()
    if args.version:
        from backend.core.runtime import get_runtime_config
        print(get_runtime_config().version)
        return 0
    if args.command == "init-config":
        try:
            _write_config(args.config, args.port)
            print(f"配置已写入 {args.config}")
            return 0
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command == "healthcheck":
        return _healthcheck(args.url)
    if args.command in {None, "serve"}:
        return _serve(getattr(args, "config", DEFAULT_CONFIG))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
