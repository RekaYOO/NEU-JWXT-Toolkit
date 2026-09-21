#!/usr/bin/env bash
set -euo pipefail

APP_NAME="neu-jwxt-toolkit"
APP_ROOT="/opt/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
REQUEST_FILE="${DATA_DIR}/updates/request.env"
LOCK_FILE="${DATA_DIR}/updates/update.lock"

[[ "${EUID}" -eq 0 ]] || exit 1
[[ -f "${REQUEST_FILE}" ]] || exit 0
request_owner="$(stat -c '%U' "${REQUEST_FILE}" 2>/dev/null || true)"
request_mode="$(stat -c '%a' "${REQUEST_FILE}" 2>/dev/null || true)"
[[ "${request_owner}" == "neu-jwxt" && "${request_mode}" == "600" ]] || exit 1
install -d -o neu-jwxt -g neu-jwxt -m 0700 "${DATA_DIR}/updates"

exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0

result_file="${DATA_DIR}/updates/update-result.env"
on_error() {
  local status=$?
  printf 'job_id=%s\nstate=failed\nerror=更新器执行失败\n' "${job_id:-unknown}" > "${result_file}"
  chown neu-jwxt:neu-jwxt "${result_file}" 2>/dev/null || true
  exit "${status}"
}
trap on_error ERR

version=""; asset=""; expected=""; job_id=""
while IFS='=' read -r key value; do
  case "${key}" in
    version) version="${value}" ;;
    asset) asset="${value}" ;;
    sha256) expected="${value}" ;;
    job_id) job_id="${value}" ;;
  esac
done < "${REQUEST_FILE}"

[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] || exit 1
[[ "${asset}" == "NEU-JWXT-Toolkit-${version}-linux-amd64.tar.gz" ]] || exit 1
[[ "${expected}" =~ ^[0-9a-fA-F]{64}$ ]] || exit 1
package="${DATA_DIR}/updates/${version}/${asset}"
[[ -f "${package}" ]] || exit 1
actual="$(sha256sum "${package}" | awk '{print tolower($1)}')"
[[ "${actual}" == "$(printf '%s' "${expected}" | tr '[:upper:]' '[:lower:]')" ]] || exit 1

extract_root="$(mktemp -d "${DATA_DIR}/updates/.extract.XXXXXX")"
cleanup() { rm -rf -- "${extract_root}"; }
trap cleanup EXIT
tar -xzf "${package}" -C "${extract_root}"
package_root="${extract_root}/neu-jwxt-toolkit"
[[ -x "${package_root}/install.sh" && -x "${package_root}/app/neu-jwxt-server" ]] || exit 1
(cd "${package_root}" && ./install.sh --upgrade)
rm -f -- "${REQUEST_FILE}"
printf 'job_id=%s\nstate=completed\nversion=%s\n' "${job_id}" "${version}" > "${DATA_DIR}/updates/update-result.env"
chown neu-jwxt:neu-jwxt "${DATA_DIR}/updates/update-result.env"
