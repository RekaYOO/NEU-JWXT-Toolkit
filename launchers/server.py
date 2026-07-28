"""Linux server launcher and lightweight configuration utility."""

from __future__ import annotations

import argparse
import getpass
import http.client
import ipaddress
import json
import os
import secrets
import sys
import urllib.parse
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
        access_log=False,
    )
    return 0


def _healthcheck_url_from_config(config_path: Path) -> str:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("服务配置必须是 JSON 对象")
    host = str(data.get("host", "127.0.0.1")).strip()
    port = int(data.get("port", 8000))
    if not 1 <= port <= 65535:
        raise ValueError("监听端口必须在 1 到 65535 之间")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        if host.lower() != "localhost":
            raise ValueError("健康检查仅允许回环监听地址") from error
    else:
        if not address.is_loopback:
            raise ValueError("健康检查仅允许回环监听地址")
    display_host = f"[{host}]" if ":" in host else host
    return f"http://{display_host}:{port}/api/health"


def _healthcheck(url: str) -> int:
    connection = None
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "http" or not parsed.hostname:
            return 2
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if parsed.hostname.lower() != "localhost":
                return 2
        else:
            if not address.is_loopback:
                return 2
        connection = http.client.HTTPConnection(
            parsed.hostname,
            parsed.port or 80,
            timeout=2,
        )
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
        return 0 if response.status == 200 else 1
    except (OSError, http.client.HTTPException, ValueError):
        return 1
    finally:
        if connection is not None:
            connection.close()


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
        default=None,
    )
    health_parser.add_argument("--config", type=Path, default=None)
    health_parser.add_argument("--print-url", action="store_true")

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
        try:
            url = (
                _healthcheck_url_from_config(args.config)
                if args.config
                else (args.url or "http://127.0.0.1:8000/api/health")
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            print(f"无法读取健康检查配置：{error}", file=sys.stderr)
            return 2
        result = _healthcheck(url)
        if result == 0 and args.print_url:
            print(url)
        return result
    if args.command in {None, "serve"}:
        return _serve(getattr(args, "config", DEFAULT_CONFIG))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
