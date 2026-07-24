#!/usr/bin/env bash
set -euo pipefail

APP_NAME="neu-jwxt-toolkit"
APP_ROOT="/opt/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
DATA_DIR="/var/lib/${APP_NAME}"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-install}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行安装脚本。" >&2
  exit 1
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "当前发行包仅支持 Linux x86_64。" >&2
  exit 1
fi

if [[ "${MODE}" != "install" && "${MODE}" != "--upgrade" ]]; then
  echo "用法: sudo ./install.sh [--upgrade]" >&2
  exit 2
fi

if [[ ! -x "${PACKAGE_ROOT}/app/neu-jwxt-server" ]]; then
  echo "发行包不完整：缺少 app/neu-jwxt-server。" >&2
  exit 1
fi

if ! id -u neu-jwxt >/dev/null 2>&1; then
  useradd --system --home-dir "${DATA_DIR}" --shell /usr/sbin/nologin neu-jwxt
fi

install -d -m 0755 "${APP_ROOT}" "${CONFIG_DIR}"
install -d -o neu-jwxt -g neu-jwxt -m 0700 "${DATA_DIR}"

BACKUP_DIR="${APP_ROOT}/app.previous"
if [[ -d "${APP_ROOT}/app" ]]; then
  systemctl stop "${APP_NAME}.service" 2>/dev/null || true
  rm -rf "${BACKUP_DIR}"
  mv "${APP_ROOT}/app" "${BACKUP_DIR}"
fi

cp -a "${PACKAGE_ROOT}/app" "${APP_ROOT}/app"
chown -R root:root "${APP_ROOT}/app"
chmod 0755 "${APP_ROOT}/app/neu-jwxt-server"
rm -rf "${APP_ROOT}/examples"
cp -a "${PACKAGE_ROOT}/examples" "${APP_ROOT}/examples"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  read -r -p "监听端口 [8000]: " PORT
  PORT="${PORT:-8000}"
  "${APP_ROOT}/app/neu-jwxt-server" init-config --config "${CONFIG_FILE}" --port "${PORT}"
fi
chown neu-jwxt:neu-jwxt "${CONFIG_FILE}"
chmod 0600 "${CONFIG_FILE}"

install -m 0644 "${PACKAGE_ROOT}/neu-jwxt-toolkit.service" "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable "${APP_NAME}.service" >/dev/null
systemctl restart "${APP_NAME}.service"

PORT="$(sed -nE 's/^[[:space:]]*"port"[[:space:]]*:[[:space:]]*([0-9]+).*/\1/p' "${CONFIG_FILE}" | head -n1)"
PORT="${PORT:-8000}"

HEALTHY=0
for _ in $(seq 1 40); do
  if "${APP_ROOT}/app/neu-jwxt-server" healthcheck \
    --url "http://127.0.0.1:${PORT}/api/health"; then
    HEALTHY=1
    break
  fi
  sleep 0.25
done

if [[ "${HEALTHY}" -ne 1 ]]; then
  echo "新版本健康检查失败。" >&2
  systemctl stop "${APP_NAME}.service" || true
  if [[ -d "${BACKUP_DIR}" ]]; then
    rm -rf "${APP_ROOT}/app"
    mv "${BACKUP_DIR}" "${APP_ROOT}/app"
    systemctl start "${APP_NAME}.service"
    echo "已恢复上一版本。" >&2
  fi
  exit 1
fi

rm -rf "${BACKUP_DIR}"
echo
echo "NEU 教务工具箱已安装并启动。"
echo "本地检查地址: http://127.0.0.1:${PORT}"
echo "Caddy 示例: ${APP_ROOT}/examples/Caddyfile"
echo "Nginx 示例: ${APP_ROOT}/examples/nginx.conf"
