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
SERVICE_BACKUP="${SERVICE_FILE}.previous"
HAD_PREVIOUS=0

rollback_install() {
  local reason="${1:-安装或升级失败}"
  set +e
  echo "${reason}，正在恢复上一状态。" >&2
  systemctl stop "${APP_NAME}.service" >/dev/null 2>&1 || true
  rm -rf "${APP_ROOT}/app"
  if [[ -d "${BACKUP_DIR}" ]]; then
    mv "${BACKUP_DIR}" "${APP_ROOT}/app"
  fi
  if [[ -f "${SERVICE_BACKUP}" ]]; then
    mv "${SERVICE_BACKUP}" "${SERVICE_FILE}"
  elif [[ "${HAD_PREVIOUS}" -eq 0 ]]; then
    rm -f "${SERVICE_FILE}"
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  if [[ "${HAD_PREVIOUS}" -eq 1 && -d "${APP_ROOT}/app" ]]; then
    systemctl start "${APP_NAME}.service" >/dev/null 2>&1 || true
    echo "已恢复上一版本的程序和 systemd 服务文件；配置与数据未改动。" >&2
  else
    echo "首次安装未成功，服务保持停止；配置与数据未删除。" >&2
  fi
}

on_install_error() {
  local status=$?
  trap - ERR
  rollback_install "安装脚本在替换程序时出错"
  exit "${status}"
}

if [[ -d "${APP_ROOT}/app" ]]; then
  HAD_PREVIOUS=1
  systemctl stop "${APP_NAME}.service" 2>/dev/null || true
  rm -rf "${BACKUP_DIR}"
  mv "${APP_ROOT}/app" "${BACKUP_DIR}"
fi

trap on_install_error ERR

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

rm -f "${SERVICE_BACKUP}"
if [[ -f "${SERVICE_FILE}" ]]; then
  cp -a "${SERVICE_FILE}" "${SERVICE_BACKUP}"
fi
install -m 0644 "${PACKAGE_ROOT}/neu-jwxt-toolkit.service" "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable "${APP_NAME}.service" >/dev/null
systemctl restart "${APP_NAME}.service" || true

HEALTHY=0
for _ in $(seq 1 120); do
  if "${APP_ROOT}/app/neu-jwxt-server" healthcheck \
    --config "${CONFIG_FILE}"; then
    HEALTHY=1
    break
  fi
  sleep 0.5
done

if [[ "${HEALTHY}" -ne 1 ]]; then
  echo "新版本健康检查失败。以下是新服务的诊断信息：" >&2
  systemctl status "${APP_NAME}.service" --no-pager -l >&2 || true
  journalctl -u "${APP_NAME}.service" -n 100 --no-pager >&2 || true
  trap - ERR
  rollback_install "新版本健康检查失败"
  exit 1
fi

trap - ERR
rm -rf "${BACKUP_DIR}"
rm -f "${SERVICE_BACKUP}"
HEALTH_URL="$("${APP_ROOT}/app/neu-jwxt-server" healthcheck \
  --config "${CONFIG_FILE}" --print-url)"
echo
echo "NEU 教务工具箱已安装并启动。"
echo "本地检查地址: ${HEALTH_URL}"
echo "Caddy 示例: ${APP_ROOT}/examples/Caddyfile"
echo "Nginx 示例: ${APP_ROOT}/examples/nginx.conf"
