#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行卸载脚本。" >&2
  exit 1
fi

systemctl disable --now neu-jwxt-toolkit.service 2>/dev/null || true
rm -f /etc/systemd/system/neu-jwxt-toolkit.service
systemctl daemon-reload
rm -rf /opt/neu-jwxt-toolkit

read -r -p "是否同时删除配置和数据？输入 DELETE 确认: " CONFIRM
if [[ "${CONFIRM}" == "DELETE" ]]; then
  rm -rf /etc/neu-jwxt-toolkit /var/lib/neu-jwxt-toolkit
  userdel neu-jwxt 2>/dev/null || true
  echo "程序、配置和数据均已删除。"
else
  echo "程序已卸载，配置和数据已保留。"
fi
