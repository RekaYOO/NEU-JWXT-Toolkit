import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

from backend.core.runtime.access import hash_access_password


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.timeout(30)
def test_server_access_gateway_and_static_frontend(tmp_path):
    port = _free_port()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "profile": "server",
                "host": "127.0.0.1",
                "port": port,
                "access_password": hash_access_password("server-test-password"),
                "session_secret": "server-test-session-secret-at-least-32-bytes",
                "trusted_proxies": ["127.0.0.1"],
            }
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["NEU_JWXT_DATA_DIR"] = str(tmp_path / "data")
    process = subprocess.Popen(
        [
            sys.executable,
            "launchers/server.py",
            "serve",
            "--config",
            str(config_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(40):
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                pytest.fail(f"server exited during startup:\n{output}")
            try:
                if requests.get(f"{base_url}/api/health", timeout=0.25).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.1)
        else:
            output = process.stdout.read() if process.stdout else ""
            pytest.fail(f"server failed to start:\n{output}")

        homepage = requests.get(
            f"{base_url}/",
            headers={"Origin": "https://untrusted.example"},
        )
        assert homepage.status_code == 200
        assert "access-control-allow-origin" not in homepage.headers
        recovery = requests.get(
            f"{base_url}/api/grade-tracking/recovery/invalid-token/status"
        )
        assert recovery.status_code == 404
        assert recovery.json()["detail"] == "一次性登录链接不存在或已失效"
        protected = requests.get(
            f"{base_url}/api/status",
            headers={
                "X-Forwarded-For": "198.51.100.23",
                "User-Agent": "security-audit-test/1.0",
            },
        )
        assert protected.status_code == 401
        assert protected.json()["code"] == "ACCESS_REQUIRED"
        offline = requests.get(f"{base_url}/api/offline/status")
        assert offline.status_code == 401
        assert offline.json()["code"] == "ACCESS_REQUIRED"

        session = requests.Session()
        login = session.post(
            f"{base_url}/api/access/login",
            json={"password": "server-test-password"},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert login.status_code == 200
        assert "Secure" in login.headers["set-cookie"]

        cookie_value = login.cookies.get("neu_jwxt_access")
        status = requests.get(
            f"{base_url}/api/access/status",
            cookies={"neu_jwxt_access": cookie_value},
        )
        assert status.json()["authenticated"] is True

        for _ in range(5):
            wrong = requests.post(
                f"{base_url}/api/access/login",
                json={"password": "wrong-password"},
            )
            assert wrong.status_code == 401
        blocked = requests.post(
            f"{base_url}/api/access/login",
            json={"password": "wrong-password"},
        )
        assert blocked.status_code == 429

        access_log = next((tmp_path / "data" / "logs").glob("access_*.log"))
        access_text = access_log.read_text(encoding="utf-8")
        assert '"path":"/api/status"' in access_text
        assert '"status_code":401' in access_text
        assert '"client_ip":"198.51.100.23"' in access_text
        assert '"peer_ip":"127.0.0.1"' in access_text
        assert '"user_agent":"security-audit-test/1.0"' in access_text

        login_log = next((tmp_path / "data" / "logs").glob("login_*.log"))
        login_text = login_log.read_text(encoding="utf-8")
        assert '"event":"access_gateway_login"' in login_text
        assert '"outcome":"failure"' in login_text
        assert '"outcome":"blocked"' in login_text
        assert '"access_session_id":"' in login_text
        assert "server-test-password" not in login_text
        assert "wrong-password" not in login_text
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
