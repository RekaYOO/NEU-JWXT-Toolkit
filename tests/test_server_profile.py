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
                "session_secret": "server-test-session-secret",
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
        protected = requests.get(f"{base_url}/api/status")
        assert protected.status_code == 401
        assert protected.json()["code"] == "ACCESS_REQUIRED"

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
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
